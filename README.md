# bulk-reducto-converter-v2

Bulk multi-type document → Markdown converter behind WorkOS AuthKit sign-in. Sign in, drop files, get a zip of `.md` files back. See [SPEC.md](SPEC.md) for the full design.

## Deployment & testing target

**This service is deployed and tested exclusively on Render.** Render is the only environment where the WorkOS auth flow, HTTPS-only cookies, and reverse-proxy headers are correctly wired. Local execution is not a supported workflow — anything that works locally but fails on Render is a bug.

The repo's [render.yaml](render.yaml) declares a Docker web service on the Standard plan with `autoDeploy: true`, so pushing to `main` is what triggers a real deploy.

## First-time setup

1. **Create the Render service** by pointing Render at this repo. Render will pick up `render.yaml` and build the Docker image.
2. **Configure environment variables in the Render dashboard** (Environment tab). The full list is in [.env.example](.env.example); the six WorkOS / auth ones are documented in [Configuration](#configuration) below and must be set with the production values described there.
3. **Configure the WorkOS Dashboard** for the application you're using:
   - Redirect URI: `https://<your-svc>.onrender.com/auth/callback`
   - Sign-in endpoint: `https://<your-svc>.onrender.com/sign-in`
   - Sign-out redirects: `https://<your-svc>.onrender.com/sign-in`
   - Inside an Organization, configure an SSO connection (the default Test Organization with verified domain `example.com` includes a Test Identity Provider that works out of the box).

The Redirect URI in `WORKOS_REDIRECT_URI` MUST match the one registered in the WorkOS Dashboard byte-for-byte. Mismatch → `invalid_grant`.

## Configuration

Render dashboard → Environment. `sync: false` entries from [render.yaml](render.yaml) require values set per service.

| Var | Default | Purpose |
| --- | --- | --- |
| `OCR` | `docling` | `docling` (local, no API key) or `reducto` (hosted). |
| `REDUCTO_API_KEY` | — | Required when `OCR=reducto` or for OCR fallback on scanned PDFs. |
| `REDUCTO_API_URL` | `https://platform.reducto.ai` | Reducto base URL. |
| `MAX_UPLOAD_BYTES` | `209715200` (200 MiB) | Cumulative batch cap. |
| `MAX_FILES_PER_JOB` | `50` | Per-request file count cap. |
| `PER_FILE_TIMEOUT_S` | `300` | Per-file conversion timeout. |
| `WORKOS_API_KEY` | — | Secret API key (`sk_test_...` or `sk_live_...`) from the WorkOS Dashboard. |
| `WORKOS_CLIENT_ID` | — | Application Client ID (`client_...`) from the WorkOS Dashboard. |
| `WORKOS_REDIRECT_URI` | — | `https://<your-svc>.onrender.com/auth/callback`. Must match the Dashboard exactly. |
| `WORKOS_COOKIE_PASSWORD` | — | ≥ 32 char random secret used to seal session cookies. Identical across instances. |
| `WORKOS_DEFAULT_ORG_ID` | — | When set, `/login` auto-routes to that org's SSO. Leave empty for the generic AuthKit page. |
| `APP_BASE_URL` | — | `https://<your-svc>.onrender.com`. The `https://` prefix drives the cookie `secure` flag. |

Render sets `$PORT` on the container automatically — `start.sh` reads it directly, so there is no `PORT` setting in the app config.

Every response carries `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, and `Referrer-Policy: same-origin` as defense-in-depth.

## Supported file types

`.md`, `.markdown`, `.txt`, `.csv`, `.docx`, `.xlsx`, `.xlsm`, `.pdf`, `.png`, `.jpg`, `.jpeg`, `.webp`, `.tiff`, `.tif`. Anything else returns a per-file error in `errors.txt` inside the output zip.

## Auth flow at a glance

| Route | Auth | Purpose |
| --- | --- | --- |
| `GET /health` | Open | Render's health probe. Returns `{"status":"ok","ocr":"..."}`. |
| `GET /sign-in` | Open | Branded login page. |
| `GET /login` | Open | Redirects to AuthKit's hosted sign-in (or directly to the SSO Test IdP if `WORKOS_DEFAULT_ORG_ID` is set). |
| `GET /auth/callback` | Open | Exchanges the auth code for a sealed session cookie, then 302 to `/`. |
| `GET\|POST /logout` | Open | Clears the cookie and bounces through AuthKit's logout. |
| `GET /` | Gated | Drag-and-drop UI. Unauthenticated → 302 to `/sign-in`. |
| `POST /convert` | Gated | Conversion API. Unauthenticated → 401 JSON. |
| `GET /me` | Gated | Returns the signed-in user dict. 401 JSON when unauthenticated. |

## Roles & UI behavior

WorkOS may return a `role` (and `roles`) on the authenticated session — these flow through `/me`. The frontend uses them for cosmetic affordances only:

- A role badge in the header (for any non-empty role).
- The dark-mode toggle in the footer is shown when `role === "admin"`.

There is no server-side RBAC. Any authenticated user can convert documents — `/convert` is a binary signed-in gate. Treat the admin badge as a hint, not a permission. If you ever need to gate the conversion endpoint by role, update SPEC.md §18 in the same change.
