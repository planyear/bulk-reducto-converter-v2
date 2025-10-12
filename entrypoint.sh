#!/usr/bin/env sh
set -e

# If the service account is provided as base64, write it to disk
if [ -n "$SERVICE_ACCOUNT_JSON_B64" ]; then
  echo "$SERVICE_ACCOUNT_JSON_B64" | base64 -d > /app/service-account.json
fi

exec "$@"
