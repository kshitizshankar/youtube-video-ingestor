"""Optional HTTP Basic-style auth gate.

- If both BASIC_AUTH_USER and BASIC_AUTH_PASS are set in the environment,
  every API route and WebSocket requires a valid token.
- If either is blank/missing, auth is a no-op (local dev default).

Accepted token forms:
- `Authorization: Basic base64(user:password)` HTTP header (for fetch)
- `?token=base64(user:password)` query parameter (for EventSource / WebSocket,
  which cannot set custom headers in browsers)

`/api/auth/status` lets the SPA probe whether auth is enabled and whether
the current token is valid; everything else is gated.
"""

from __future__ import annotations

import base64
import os
import secrets

from fastapi import HTTPException, Request, WebSocket, status


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def is_enabled() -> bool:
    return bool(_env("BASIC_AUTH_USER") and _env("BASIC_AUTH_PASS"))


def _check_token(token: str) -> bool:
    try:
        decoded = base64.b64decode(token, validate=False).decode("utf-8", "replace")
    except Exception:
        return False
    user, sep, password = decoded.partition(":")
    if not sep:
        return False
    return (
        secrets.compare_digest(user, _env("BASIC_AUTH_USER"))
        and secrets.compare_digest(password, _env("BASIC_AUTH_PASS"))
    )


def _extract(request_headers: dict | None, query_token: str | None) -> str | None:
    if query_token:
        return query_token
    if request_headers:
        auth = request_headers.get("authorization") or request_headers.get("Authorization")
        if auth and auth.startswith("Basic "):
            return auth[6:]
    return None


def require_http(request: Request) -> None:
    """FastAPI dependency: raises 401 if auth is enabled and the request
    doesn't carry a valid token."""
    if not is_enabled():
        return
    tok = _extract(
        dict(request.headers),
        request.query_params.get("token"),
    )
    if tok and _check_token(tok):
        return
    # Intentionally no WWW-Authenticate header: it causes browsers to pop up
    # their native Basic Auth dialog instead of our in-app LoginGate.
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="auth required",
    )


def check_ws(ws: WebSocket) -> bool:
    """Check a WebSocket handshake. Returns True if the request is
    authorized (or auth is disabled)."""
    if not is_enabled():
        return True
    tok = _extract(dict(ws.headers), ws.query_params.get("token"))
    return bool(tok and _check_token(tok))
