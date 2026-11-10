from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import UserRole


def _check_email(value: str) -> str:
    value = value.strip().lower()
    local, _, domain = value.partition("@")
    if not local or "." not in domain or " " in value or len(value) > 255:
        raise ValueError("Enter a valid email address")
    return value


class LoginRequest(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(min_length=1, max_length=200)

    _email = field_validator("email")(_check_email)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    totp_enabled: bool = False
    last_login_at: datetime | None
    created_at: datetime


class LoginResponse(BaseModel):
    user: UserOut
    csrf_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class CreateUserRequest(BaseModel):
    email: str = Field(max_length=255)
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=200)
    role: UserRole = UserRole.STAFF

    _email = field_validator("email")(_check_email)


class UpdateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool | None = None
    role: UserRole | None = None


class MfaLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mfa_token: str = Field(min_length=10, max_length=300)
    code: str | None = Field(default=None, max_length=12)
    recovery_code: str | None = Field(default=None, max_length=20)


class MfaCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=6, max_length=12)


class MfaDisableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=12)
    recovery_code: str | None = Field(default=None, max_length=20)


class DemoLoginStatus(BaseModel):
    enabled: bool
