"""Transaction passport contracts."""

from typing import Any

from pydantic import BaseModel, Field


class PassportSignatureOut(BaseModel):
    """The tag that makes the body tamper-evident."""

    algorithm: str
    body_hash: str
    value: str


class PassportOut(BaseModel):
    """A complete, signed passport.

    The body is passed through as a plain mapping rather than modelled field by
    field. A schema that reshaped it would be a second source of truth for what
    is signed, and the first mismatch between the two would make every
    passport fail to verify.
    """

    body: dict[str, Any]
    signature: PassportSignatureOut


class PassportVerifyRequest(BaseModel):
    """A passport submitted for checking."""

    body: dict[str, Any]
    signature: dict[str, Any]


class PassportVerifyOut(BaseModel):
    """Whether a submitted passport holds up, and why not if it does not."""

    valid: bool
    errors: list[str] = Field(default_factory=list)
