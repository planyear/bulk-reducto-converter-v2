"""WorkOS AuthKit integration: client + auth dependency.

The shipped workos SDK (v7) does NOT take a `session={"seal_session": True}`
parameter on `authenticate_with_code`, and `AuthenticateResponse` has no
`sealed_session` attribute. Sealing is done via the module-level helper
`workos.session.seal_session_from_auth_response` after the code exchange. See
app/auth_routes.py for the callback that uses it.
"""
import logging
import secrets

from fastapi import HTTPException, Request, status
from workos import WorkOSClient

from app.config import settings

log = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "wos_session"
OAUTH_STATE_COOKIE_NAME = "wos_oauth_state"

_client: WorkOSClient | None = None


def get_workos_client() -> WorkOSClient:
    """Lazily build the WorkOSClient so importing this module does not fail
    when env vars are not yet configured (e.g. during smoke imports or in
    environments where Settings has not been populated).
    """
    global _client
    if _client is None:
        _require_config()
        _client = WorkOSClient(
            api_key=settings.WORKOS_API_KEY,
            client_id=settings.WORKOS_CLIENT_ID,
        )
    return _client


def _require_config() -> None:
    missing = [
        name for name in ("WORKOS_API_KEY", "WORKOS_CLIENT_ID",
                          "WORKOS_REDIRECT_URI", "WORKOS_COOKIE_PASSWORD")
        if not getattr(settings, name)
    ]
    if missing:
        raise RuntimeError(f"WorkOS config incomplete: missing {missing}")
    if len(settings.WORKOS_COOKIE_PASSWORD) < 32:
        raise RuntimeError("WORKOS_COOKIE_PASSWORD must be >= 32 characters")


def new_state() -> str:
    return secrets.token_urlsafe(32)


def _user_field(user, key):
    """Read a field off the unsealed user. The v7 SDK types the user on the
    session-cookie success response as `Dict[str, Any]`, but historically some
    paths returned a dataclass-like object. Handle both forms."""
    if isinstance(user, dict):
        return user.get(key)
    return getattr(user, key, None)


def get_authenticated_user(request: Request) -> dict:
    sealed = request.cookies.get(SESSION_COOKIE_NAME)
    if not sealed:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Not authenticated")
    try:
        session = get_workos_client().user_management.load_sealed_session(
            session_data=sealed,
            cookie_password=settings.WORKOS_COOKIE_PASSWORD,
        )
        auth = session.authenticate()
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Sealed-session decode failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid session") from exc

    if not getattr(auth, "authenticated", False) or not getattr(auth, "user", None):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Session expired")

    user = auth.user
    return {
        "id": _user_field(user, "id"),
        "email": _user_field(user, "email"),
        "first_name": _user_field(user, "first_name"),
        "last_name": _user_field(user, "last_name"),
        "organization_id": getattr(auth, "organization_id", None),
        "role": getattr(auth, "role", None),
        "roles": list(getattr(auth, "roles", None) or []),
    }
