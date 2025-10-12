#!/bin/sh
set -e

# If service account is provided, write the JSON file
if [ -n "$SERVICE_ACCOUNT_JSON_B64" ]; then
  echo "$SERVICE_ACCOUNT_JSON_B64" | base64 -d > /app/service-account.json
fi

# Start uvicorn on the port Render provides
exec python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --proxy-headers \
  --forwarded-allow-ips="*"
