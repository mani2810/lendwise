"""LendWise: one FastAPI process serves the API and the HTML/CSS/JS frontend.

    cd backend && uvicorn app.main:app      ->  http://127.0.0.1:8000
"""
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from .api.routes import admin, applications, auth
from .bootstrap import ensure_admin, init_db, seed_bureau, warn_insecure_defaults
from .core.config import BACKEND_DIR, FRONTEND_DIR, get_settings
from .core.logging import request_id_var, setup_logging
from .core.ratelimit import limiter
from .db.session import SessionLocal
from .llm.gateway import llm_status
from .ml.model import get_model
from .services import lending

settings = get_settings()
setup_logging(settings.log_level, settings.log_format, settings.log_file, base_dir=BACKEND_DIR)
log = logging.getLogger("app")

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; font-src 'self'; "
        "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": _CSP,
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with SessionLocal() as db:
        ensure_admin(db)
        seed_bureau(db)
        lending.active_policy(db)
        warn_insecure_defaults(db)
    model = get_model()                      # trains once on a fresh clone (~10 s)
    lending.recover()
    llm = llm_status()
    log.info("startup complete", extra={"env": settings.environment, "llm": llm["mode"], "llm_ready": llm["ready"],
                                        "model": model.version, "db": settings.database_url.split(":")[0]})
    if not llm["ready"]:
        log.warning("LLM not ready: %s", llm.get("problem"))
    yield


app = FastAPI(
    title=f"{settings.app_name} API",
    version="1.0.0",
    description="AI-assisted personal-loan underwriting: a tool-calling agent gathers evidence, code-enforced "
                "policy and an explainable risk model recommend, a human underwriter decides.",
    lifespan=lifespan,
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
# No CORS middleware on purpose: the frontend is same-origin; other origins get no CORS grants.


@app.middleware("http")
async def request_context(request: Request, call_next):
    incoming = request.headers.get("X-Request-ID", "")
    rid = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex[:12]
    request_id_var.set(rid)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled error")
        response = JSONResponse({"detail": "Internal server error", "request_id": rid}, status_code=500)
    response.headers["X-Request-ID"] = rid
    path = request.url.path
    for name, value in _SECURITY_HEADERS.items():
        if path.startswith("/docs") and name == "Content-Security-Policy":
            continue                                    # Swagger UI loads its own scripts from a CDN
        response.headers.setdefault(name, value)
    if path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    elif not path.startswith("/docs"):
        response.headers.setdefault("Cache-Control", "no-cache")   # static files: always revalidate (ETag)
    if path.startswith("/api/") or response.status_code >= 400:
        level = logging.WARNING if response.status_code >= 500 else logging.INFO
        log.log(level, "request", extra={"method": request.method, "path": path, "status": response.status_code,
                                         "duration_ms": round((time.perf_counter() - start) * 1000, 1)})
    return response


API = "/api/v1"
for r in (auth.router, applications.router, admin.router):
    app.include_router(r, prefix=API)


@app.get("/health", tags=["ops"])
def health():
    return {"status": "ok"}


@app.get("/ready", tags=["ops"])
def ready():
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    llm = llm_status()
    return {"status": "ready", "llm": llm["mode"], "llm_ready": llm["ready"],
            "breakers": llm.get("breakers", {})}


@app.get("/api/v1/meta", tags=["ops"])
def meta():
    llm = llm_status()
    return {"app": settings.app_name, "allow_registration": settings.allow_registration,
            "llm_ready": llm.get("ready", False), "llm_mode": settings.llm_mode, "llm_problem": llm.get("problem"),
            "purposes": lending.PURPOSES, "four_eyes_amount": settings.four_eyes_amount}


if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
