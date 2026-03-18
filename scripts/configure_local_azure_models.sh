#!/usr/bin/env bash

set -euo pipefail

WEBUI_URL="${WEBUI_URL:-http://127.0.0.1:8080}"
WEBUI_ADMIN_EMAIL="${WEBUI_ADMIN_EMAIL:-admin@local.test}"
WEBUI_ADMIN_PASSWORD="${WEBUI_ADMIN_PASSWORD:-admin123456}"

API1_ENDPOINT="${API1_ENDPOINT:-https://agl-dev.cognitiveservices.azure.com}"
API1_VERSION="${API1_VERSION:-2025-04-01-preview}"
API1_MODEL="${API1_MODEL:-gpt-5.4}"
API1_KEY="${AZURE_OPENAI_API_KEY_1:-}"

API0_ENDPOINT="${API0_ENDPOINT:-https://linjl-ma65uv6u-eastus2.cognitiveservices.azure.com}"
API0_VERSION="${API0_VERSION:-2025-01-01-preview}"
API0_MODEL="${API0_MODEL:-gpt-5.4-pro}"
API0_KEY="${OPENAI_API_KEY_PLAIN:-}"

if [[ -z "${API1_KEY}" ]]; then
  echo "[ERROR] Missing AZURE_OPENAI_API_KEY_1 for ${API1_MODEL}" >&2
  exit 1
fi

if [[ -z "${API0_KEY}" ]]; then
  echo "[ERROR] Missing OPENAI_API_KEY_PLAIN for ${API0_MODEL}" >&2
  exit 1
fi

echo "==> Signing in to Open WebUI: ${WEBUI_URL}"
TOKEN="$(
  curl -fsS -X POST "${WEBUI_URL}/api/v1/auths/signin" \
    -H 'Content-Type: application/json' \
    -d "{\"email\":\"${WEBUI_ADMIN_EMAIL}\",\"password\":\"${WEBUI_ADMIN_PASSWORD}\"}" |
    python3 -c 'import sys, json; print(json.load(sys.stdin)["token"])'
)"

echo "==> Applying Azure model connections"
PAYLOAD="$(
  API1_ENDPOINT="${API1_ENDPOINT}" \
  API1_VERSION="${API1_VERSION}" \
  API1_MODEL="${API1_MODEL}" \
  API1_KEY="${API1_KEY}" \
  API0_ENDPOINT="${API0_ENDPOINT}" \
  API0_VERSION="${API0_VERSION}" \
  API0_MODEL="${API0_MODEL}" \
  API0_KEY="${API0_KEY}" \
  python3 - <<'PY'
import json
import os

payload = {
    "ENABLE_OPENAI_API": True,
    "OPENAI_API_BASE_URLS": [
        os.environ["API1_ENDPOINT"],
        os.environ["API0_ENDPOINT"],
    ],
    "OPENAI_API_KEYS": [
        os.environ["API1_KEY"],
        os.environ["API0_KEY"],
    ],
    "OPENAI_API_CONFIGS": {
        "0": {
            "azure": True,
            "api_version": os.environ["API1_VERSION"],
            "auth_type": "bearer",
            "api_type": "responses_v1",
            "api_key_header": "authorization",
            "model_ids": [os.environ["API1_MODEL"]],
            "connection_type": "external",
        },
        "1": {
            "azure": True,
            "api_version": os.environ["API0_VERSION"],
            "auth_type": "bearer",
            "api_type": "responses_v1",
            "api_key_header": "authorization",
            "model_ids": [os.environ["API0_MODEL"]],
            "connection_type": "external",
        },
    },
}

print(json.dumps(payload))
PY
)"

curl -fsS -X POST "${WEBUI_URL}/openai/config/update" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${TOKEN}" \
  -d "${PAYLOAD}" >/dev/null

echo "==> Fetching configured model list"
MODELS_JSON="$(
  curl -fsS "${WEBUI_URL}/openai/models" \
    -H "Authorization: Bearer ${TOKEN}"
)"

printf '%s' "${MODELS_JSON}" | python3 -c '
import json
import sys

data = json.load(sys.stdin)
models = [item["id"] for item in data.get("data", [])]

print("Configured models:")
for model in models:
    print(f"- {model}")
'

echo "==> Azure model configuration updated successfully"
