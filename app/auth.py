from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.openapi.docs import get_swagger_ui_html
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth
from .config import settings

# OAuth registry
oauth = OAuth()
if settings.google_client_id and settings.google_client_secret:
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

def require_user(request: Request):
    user = request.session.get("user")
    if not user:
        # Build a proper login URL and attach the "next" param
        login_url = request.url_for("login").include_query_params(next=str(request.url))
        # Redirect as a normal GET (302) so Swagger follows cleanly
        raise HTTPException(status_code=302, headers={"Location": str(login_url)})

    email = user.get("email", "")
    if not email.endswith("@" + settings.allowed_domain):
        raise HTTPException(status_code=403, detail="Forbidden: company email required")
    return user

async def login(request: Request, next: str | None = "/docs"):
    if not settings.google_client_id:
        raise HTTPException(500, "Login required")
    redirect_uri = request.url_for("auth_callback")
    request.session["post_login_redirect"] = next or "/docs"
    return await oauth.google.authorize_redirect(request, redirect_uri)


async def auth_callback(request: Request):
    token = await oauth.google.authorize_access_token(request)
    userinfo = token.get("userinfo")
    if not userinfo:
        # Some libs place id_token claims here; but for Google OIDC, userinfo should be populated
        raise HTTPException(400, "Failed to retrieve user info")
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
