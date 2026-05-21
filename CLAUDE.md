# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this service is

Stateless HTTP service that accepts a batch of mixed-type documents, converts each to Markdown, and returns a zip. Single user-facing capability: drag-and-drop (or `POST /convert`) → download zip. Sized for Render Standard (2 GB RAM / 1 CPU) — concurrency > 1 will OOM, so [app/jobs.py](app/jobs.py) uses a process-wide `asyncio.Semaphore(1)`.

A WorkOS AuthKit login gate sits in front of `/` and `/convert`. `/health` stays open for Render's probe.

## Deployment & verification target

**Render is the only supported target. Behavior on localhost is not authoritative — anything that works locally but fails on Render is a bug. Verify every behavior change against the deployed Render service before declaring it done.** The repo is wired so `autoDeploy: true` in [render.yaml](render.yaml) makes a push to `main` the trigger for a real test. Local `uvicorn` is useful only for catching import errors and syntax mistakes — not for proving features work, because the WorkOS Redirect URI, cookie `secure` flag, and reverse-proxy headers all behave differently off Render.

## Authoritative references — read these before changing anything

| File | When to read |
|---|---|
| [SPEC.md](SPEC.md) | Conversion behavior — handler output formats are specified byte-for-byte. If your change touches `app/handlers/*`, `app/jobs.py`, `app/parsers.py`, `app/packaging.py`, or `app/routing.py`, follow SPEC.md exactly. |
| [.agents/skills/workos/SKILL.md](.agents/skills/workos/SKILL.md) | WorkOS skill router. The skill references override anything else if they conflict. |
| [.agents/skills/workos/references/](.agents/skills/workos/references/) | Per-feature WorkOS references (sso, authkit-base, terms, etc.). |

## Workflow

```powershell
# Static checks — the only thing useful to run locally
pip install -r requirements.txt
python -m py_compile app/main.py app/auth.py app/auth_routes.py
python -c "from app.main import app; print(len(app.routes))"
```

Then commit and push. `git push` triggers an auto-deploy on Render; verify behavior against `https://<svc>.onrender.com`. There is no test suite, no linter config, no formatter config. The only acceptable verification is hitting the deployed Render URL in an incognito browser and walking through the sign-in + convert flow end-to-end.

Why not local uvicorn? The WorkOS Redirect URI registered in the Dashboard, the cookie `secure` flag (driven by `APP_BASE_URL`), and uvicorn's reverse-proxy handling are all production-only concerns. A local server can pass and the deployed one still break.

## Architecture

### Two concerns, layered:

1. **Conversion pipeline** (untouched by auth — treat it as a black box you don't modify unless SPEC.md drives it):
   `POST /convert` → [app/jobs.py](app/jobs.py)`.process_batch` → dispatch via [app/routing.py](app/routing.py)`.HANDLERS` (extension → callable) → per-handler in [app/handlers/](app/handlers/) → [app/packaging.py](app/packaging.py)`.build_zip`. PDFs/images go through [app/parsers.py](app/parsers.py), which dispatches to `docling` (local, default) or `reducto` (hosted) based on `OCR` env var, with optional Reducto fallback for empty docling output. Per-file failures are isolated and appended to `errors.txt` inside the output zip.

2. **Auth gate**:
   [app/auth.py](app/auth.py) holds the lazy `WorkOSClient` and the `get_authenticated_user` dependency. [app/auth_routes.py](app/auth_routes.py) defines `/sign-in`, `/login`, `/auth/callback`, `/logout`, `/me`. [app/main.py](app/main.py)'s `/` does an explicit cookie check (not `Accept`-header-based) and 302s to `/sign-in` when there's no valid `wos_session`. `/convert` and `/me` use `Depends(get_authenticated_user)` and return JSON 401 (frontend handles the redirect client-side).

### Non-obvious things

- **WorkOS Python SDK v7 sealed-session flow** — `authenticate_with_code` does NOT accept a `session={"seal_session": True, ...}` parameter (the docs at `workos.com/docs/authkit/vanilla/python` are stale). Sealing is a separate `workos.session.seal_session_from_auth_response()` call. `load_sealed_session` uses `session_data=`, NOT `sealed_session=`. `AuthenticateResponse.user` is a `@dataclass(slots=True)` — convert with `.to_dict()` before passing to the sealing helper. See the docstring in [app/auth_routes.py](app/auth_routes.py).

- **`WorkOSClient` is constructed lazily** — the SDK validates `api_key` / `client_id` at constructor time, so building it at import would crash any environment without those env vars set. [app/auth.py](app/auth.py)'s `get_workos_client()` defers construction until first use.

- **Static mount is hardened** — `app.mount("/static", ...)` uses `_AssetsOnly` (subclass of `StaticFiles`) which 404s any `.html` path. Without this, `/static/index.html` would expose the converter UI bypassing auth.

- **WorkOS dual-configuration pattern** — env vars on Render AND Redirect URIs in the WorkOS Dashboard must both be set to the production URL. Dashboard match is byte-exact (scheme, host, port, trailing slash). Mismatch → `invalid_grant`. Local `.env` and Render env vars are independent — both URLs can be registered simultaneously.

- **`start.sh` line endings** — must be LF. [.gitattributes](.gitattributes) enforces this for `*.sh` and `Dockerfile`. CRLF causes `/bin/sh^M: bad interpreter` inside the Linux container.

- **uvicorn behind Render's reverse proxy** — `start.sh` passes `--proxy-headers --forwarded-allow-ips '*'`. The single-quoted `*` is literal in `/bin/sh`.

- **Docling models are baked into the Docker image** at build time (see [Dockerfile](Dockerfile)). The build is slow (~258 MB download) but cold starts are fast.

- **RBAC is UI-only.** `/me` returns `role` and `roles` from the WorkOS sealed session and the frontend uses them to gate the header badge and the dark-mode toggle (`role === "admin"`). `/convert` does NOT check role — anyone signed in can convert. If you ever wire server-side RBAC, update SPEC.md §18 in the same commit.

- **Security headers** are set by a single middleware in [app/main.py](app/main.py): `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`. CSP and HSTS are intentionally NOT set here — CSP would block the inline `<script>` blocks in [frontend/index.html](frontend/index.html), and HSTS belongs at Render's edge.

## Environment

Settings are pydantic-settings backed; see [app/config.py](app/config.py). All production values live in the Render dashboard (Environment tab) with `sync: false` per [render.yaml](render.yaml). [.env.example](.env.example) documents every variable.

Key auth-related vars (all must be set in the Render dashboard):
- `WORKOS_REDIRECT_URI` — must MATCH a registered Redirect URI in the WorkOS Dashboard exactly (byte-for-byte: scheme, host, port, trailing slash).
- `WORKOS_COOKIE_PASSWORD` — ≥ 32 chars, IDENTICAL across all app instances. Rotating invalidates all sessions.
- `WORKOS_DEFAULT_ORG_ID` — when set, `/login` auto-routes to that org's SSO connection. Empty = AuthKit's generic hosted page.
- `APP_BASE_URL` — must start with `https://` so the cookie `secure` flag is set; cookies set without `secure` over HTTPS won't be sent back.

## Deployment

Render Standard via [render.yaml](render.yaml). `autoDeploy: true` — push to `main` triggers rebuild. `healthCheckPath: /health` (unauthenticated by design). Six WorkOS env vars are declared with `sync: false`; their values must be set in the Render dashboard. The WorkOS Dashboard must also have the production Redirect URI registered (`https://<svc>.onrender.com/auth/callback`); a mismatch returns `invalid_grant`.

## Notes
- This project will always be tested on Render Standard. Ensure the code works on that platform first and foremost
- The Render URL is https://bulk-reducto-converter-v2.onrender.com, use this
- When implementing functionalities, keep the code structured and clean. Do not slap on code solely to fix the problem, think of how it affects the rest of the code