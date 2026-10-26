"""Shared FastAPI dependencies: DB session, current user, role guards, object-level access.

Access model (deny by default):
    applicant    only their own applications (404 for anyone else's: ids are not confirmed)
    underwriter  every application, decisions, what-if, case Q&A
    admin        everything above + second approvals, policy versions, users, dashboard
"""
import logging
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..core.security import decode_access_token
from ..db.models import STAFF, Application, User
from ..db.session import get_db

bearer = HTTPBearer(auto_error=False)
DB = Annotated[Session, Depends(get_db)]
audit = logging.getLogger("audit")


def current_user(request: Request, db: DB,
                 creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated",
                                 headers={"WWW-Authenticate": "Bearer"})
    if creds is None:
        raise unauthorized
    try:
        payload = decode_access_token(creds.credentials)
    except jwt.PyJWTError:
        raise unauthorized
    user = db.get(User, int(payload["sub"]))
    if not user or not user.is_active:
        raise unauthorized
    request.state.auth = "session"
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def _require(roles: tuple[str, ...], label: str):
    def guard(user: CurrentUser) -> User:
        if user.role not in roles:
            audit.warning("forbidden", extra={"user_id": user.id, "role": user.role, "needs": label})
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"{label} only")
        return user
    return guard


Staff = Annotated[User, Depends(_require(STAFF, "Underwriters"))]
Admin = Annotated[User, Depends(_require(("admin",), "Admins"))]
Applicant = Annotated[User, Depends(_require(("applicant",), "Applicants"))]
SessionUser = CurrentUser


def get_application(app_id: int, db: DB, user: CurrentUser) -> Application:
    a = db.get(Application, app_id)
    if not a or (user.role == "applicant" and a.applicant_id != user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    return a


VisibleApplication = Annotated[Application, Depends(get_application)]
