"""API request / response models."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    name: str
    role: str
    is_active: bool
    created_at: datetime


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=200)


class Password(BaseModel):
    password: str = Field(..., min_length=10, max_length=128)

    @field_validator("password")
    @classmethod
    def strong(cls, v: str) -> str:
        if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
            raise ValueError("use at least 10 characters with letters and digits")
        return v


class RegisterIn(Password):
    email: EmailStr
    name: str = Field(..., min_length=1, max_length=100)


class PasswordChangeIn(Password):
    current_password: str = Field(..., min_length=1, max_length=200)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class DecisionIn(BaseModel):
    action: Literal["approve", "counter", "decline", "request_docs"]
    note: str = Field("", max_length=1000)
    amount: float | None = Field(None, ge=50_000, le=4_000_000)
    tenure_months: int | None = Field(None, ge=12, le=60)


class SecondApprovalIn(BaseModel):
    approve: bool
    note: str = Field("", max_length=1000)


class WhatIfIn(BaseModel):
    amount: float = Field(..., ge=50_000, le=4_000_000)
    tenure_months: int = Field(..., ge=12, le=60)


class OfferResponseIn(BaseModel):
    accept: bool


class QuestionIn(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)


class PolicyIn(BaseModel):
    rules: dict
    note: str = Field(..., min_length=3, max_length=300)


class StaffUserIn(RegisterIn):
    role: Literal["underwriter", "admin"] = "underwriter"


class UserUpdateIn(BaseModel):
    is_active: bool | None = None
