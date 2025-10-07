from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.openapi.docs import get_swagger_ui_html
from authlib.integrations.starlette_client import OAuth
from .config import settings

oauth = OAuth()

# Register Google explicitly (no discovery)
if settings.google_client_id and settings.google_client_secret:
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        access_token_url="https://oauth2.googleapis.com/token",
        jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
        userinfo_endpoint="https://openidconnect.googleapis.com/v1/userinfo",
        client_kwargs={"scope": "openid email profile", "prompt": "select_account"},
    )

def _callback_url(request: Request) -> str:
    base = getattr(settings, "public_base_url", None)
    if base:
        return base.rstrip("/") + "/auth/callback"
    return str(request.url_for("auth_callback"))

def require_user(request: Request):
    user = request.session.get("user")
    if not user:
        login_url = request.url_for("login").include_query_params(next=str(request.url))
        raise HTTPException(status_code=302, headers={"Location": str(login_url)})
    email = user.get("email", "")
    if not email.endswith("@" + settings.allowed_domain):
        raise HTTPException(status_code=403, detail="Forbidden: company email required")
    return user

async def login(request: Request, next: str | None = "/docs"):
    client = oauth.create_client("google")  # returns None if not registered
    if client is None:
        raise HTTPException(500, "Google OAuth is not configured (check env vars).")
    request.session["post_login_redirect"] = next or "/docs"
    redirect_uri = _callback_url(request)
    return await client.authorize_redirect(request, redirect_uri)

async def auth_callback(request: Request):
    client = oauth.create_client("google")
    if client is None:
        raise HTTPException(500, "Google OAuth is not configured (check env vars).")
    token = await client.authorize_access_token(request)

    # Prefer id_token claims; fall back to userinfo endpoint
    userinfo = token.get("userinfo")
    if not userinfo:
        try:
            userinfo = await client.parse_id_token(request, token)
        except Exception:
            resp = await client.get("userinfo", token=token)
            userinfo = resp.json()

    if not userinfo or "email" not in userinfo:
        raise HTTPException(400, "Failed to retrieve Google user info")

    request.session["user"] = {"email": userinfo["email"], "name": userinfo.get("name")}
    dest = request.session.pop("post_login_redirect", "/docs")
    return RedirectResponse(url=dest)

def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/")

def swagger_ui(request: Request, user=Depends(require_user)):
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="Bulk Reducto Converter – Internal",
        oauth2_redirect_url="/auth/callback",
    )
