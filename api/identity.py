"""Resolve the Command Bridge user from the Cloudflare Access email header."""

from __future__ import annotations

import os
from dataclasses import dataclass


ACCESS_EMAIL_HEADER = "Cf-Access-Authenticated-User-Email"
GIORGIO_EMAIL = "giorgio_falcone@yahoo.it"


class UnknownHermesUser(PermissionError):
    """Raised when Cloudflare supplied an email that Hermes does not allow."""


@dataclass(frozen=True)
class HermesIdentity:
    user: str
    prime_session_id: str


_IDENTITIES = {
    "giorgio": HermesIdentity("giorgio", "hermes-prime"),
    "tom": HermesIdentity("tom", "hermes-prime-tom"),
}


def resolve_email(email: str | None, *, tom_email: str | None = None) -> HermesIdentity:
    normalized = str(email or "").strip().casefold()
    if not normalized or normalized == GIORGIO_EMAIL.casefold():
        return _IDENTITIES["giorgio"]
    configured_tom = str(tom_email if tom_email is not None else os.getenv("HERMES_TOM_EMAIL", ""))
    configured_tom = configured_tom.strip().casefold()
    if configured_tom and normalized == configured_tom:
        return _IDENTITIES["tom"]
    raise UnknownHermesUser("Cloudflare Access user is not authorized for Hermes")


def resolve_request_identity(handler) -> HermesIdentity:
    cached = getattr(handler, "hermes_identity", None)
    if isinstance(cached, HermesIdentity):
        return cached
    headers = getattr(handler, "headers", None)
    email = headers.get(ACCESS_EMAIL_HEADER) if headers is not None else None
    if not isinstance(email, str):
        email = None
    identity = resolve_email(email)
    try:
        handler.hermes_identity = identity
    except (AttributeError, TypeError):
        # Focused unit-test doubles may be immutable bare ``object()`` values.
        pass
    return identity
