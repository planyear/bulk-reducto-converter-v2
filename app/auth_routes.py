"""WorkOS AuthKit routes: /login, /auth/callback, /logout, /me, /sign-in.

Implementation notes:
- The shipped workos SDK (v7) does NOT accept `session={...}` on
  authenticate_with_code, contrary to the stale Python AuthKit quickstart at
  workos.com/docs/authkit/vanilla/python. The callback below exchanges the code
  and then calls `seal_session_from_auth_response` from workos.session directly
  to produce the sealed cookie value.
- load_sealed_session uses `session_data=`, not `sealed_session=`.
- User and Impersonator on AuthenticateResponse are @dataclass(slots=True) with
  .to_dict() methods; the sealing helper expects plain dicts.
"""
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, RedirectResponse
from workos.session import seal_session_from_auth_response

from app.auth import (
    OAUTH_STATE_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    _require_config,
    get_authenticated_user,
    get_workos_client,
    new_state,
)
from app.config import settings

log = logging.getLogger(__name__)
router = APIRouter()

STATE_TTL_SECONDS = 600
_FRONTEND = Path(__file__).parent.parent / "frontend"


def _cookie_secure() -> bool:
    return not settings.APP_BASE_URL.startswith("http://localhost")


@router.get("/sign-in")
def sign_in_page():
    """Branded login landing page; reuses frontend/style.css."""
    return FileResponse(_FRONTEND / "login.html")


@router.get("/login")
def login(request: Request, organization_id: str | None = None):
    """Send the user to AuthKit's hosted sign-in page.

    Organization selection precedence (first non-empty wins):
      1. ?organization_id= query param (per-request override)
      2. settings.WORKOS_DEFAULT_ORG_ID (env default — e.g. Test Org)
      3. None → AuthKit's generic hosted page (email/password/social)
    """
    _require_config()
    state = new_state()
    kwargs: dict = {
        "provider": "authkit",
        "redirect_uri": settings.WORKOS_REDIRECT_URI,
        "state": state,
    }
    effective_org = organization_id or settings.WORKOS_DEFAULT_ORG_ID or None
    if effective_org:
        kwargs["organization_id"] = effective_org
    email = request.query_params.get("email")
    if email:
        kwargs["login_hint"] = email

    url = get_workos_client().user_management.get_authorization_url(**kwargs)

    response = RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        OAUTH_STATE_COOKIE_NAME,
        state,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
    )
    return response


@router.get("/auth/callback")
def auth_callback(request: Request):
    _require_config()

    err = request.query_params.get("error")
    if err:
        log.info("AuthKit returned error=%s", err)
        return RedirectResponse(url=f"/sign-in?error={err}", status_code=302)

    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing code parameter")

    returned_state = request.query_params.get("state")
    expected_state = request.cookies.get(OAUTH_STATE_COOKIE_NAME)
    if returned_state == "":
        # IdP-initiated flow: state is intentionally empty; skip CSRF check.
        pass
    elif not expected_state or returned_state != expected_state:
        raise HTTPException(status_code=400, detail="Invalid state")

    client = get_workos_client()
    try:
        auth_response = client.user_management.authenticate_with_code(code=code)
    except Exception as exc:
        log.warning("authenticate_with_code failed: %s", exc)
        return RedirectResponse(url="/sign-in?error=auth_failed", status_code=302)

    sealed = seal_session_from_auth_response(
        access_token=auth_response.access_token,
        refresh_token=auth_response.refresh_token,
        user=auth_response.user.to_dict(),
        impersonator=auth_response.impersonator.to_dict() if auth_response.impersonator else None,
        cookie_password=settings.WORKOS_COOKIE_PASSWORD,
    )

    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        sealed,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
    )
    response.delete_cookie(OAUTH_STATE_COOKIE_NAME)
    return response


@router.get("/logout")
@router.post("/logout")
def logout(request: Request):
    sealed = request.cookies.get(SESSION_COOKIE_NAME)
    return_to = settings.APP_BASE_URL.rstrip("/") + "/sign-in"
    target = return_to
    if sealed:
        try:
            session = get_workos_client().user_management.load_sealed_session(
                session_data=sealed,
                cookie_password=settings.WORKOS_COOKIE_PASSWORD,
            )
            target = session.get_logout_url(return_to=return_to)
        except Exception as exc:
            log.info("logout: could not load session, falling back: %s", exc)
            target = return_to

    response = RedirectResponse(url=target, status_code=status.HTTP_302_FOUND)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@router.get("/me")
def me(request: Request):
    """Returns the current signed-in user, or 401. Used by the frontend
    header to render the user's email + a Logout link.
    """
    return get_authenticated_user(request)
