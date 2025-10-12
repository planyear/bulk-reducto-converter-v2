#!/bin/sh
set -e

# Always run from the app dir
cd /app

# If the service account is provided, write it where the app expects it
if [ -n "$SERVICE_ACCOUNT_JSON_B64" ]; then
  echo "$SERVICE_ACCOUNT_JSON_B64" | base64 -d > service-account.json
fi

# Start uvicorn on Render’s injected port
exec python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --proxy-headers \
  --forwarded-allow-ips="*"
