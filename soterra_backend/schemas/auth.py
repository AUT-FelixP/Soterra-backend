from __future__ import annotations

from pydantic import Field

from .base import StrictRequestModel


class RegisterAccountRequest(StrictRequestModel):
    tenantName: str | None = Field(default=None, min_length=1, max_length=160)
    company: str | None = Field(default=None, min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=160)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=256)


class LoginRequest(StrictRequestModel):
    email: str | None = Field(default=None, min_length=3, max_length=254)
    username: str | None = Field(default=None, min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class ForgotPasswordRequest(StrictRequestModel):
    email: str = Field(min_length=3, max_length=254)


class ResetPasswordRequest(StrictRequestModel):
    token: str = Field(min_length=16, max_length=512)
    password: str = Field(min_length=12, max_length=256)


class InviteMemberRequest(StrictRequestModel):
    name: str = Field(min_length=1, max_length=160)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=256)
