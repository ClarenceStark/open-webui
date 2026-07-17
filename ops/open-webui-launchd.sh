#!/bin/zsh

set -eo pipefail

export HOME=/Users/clarencestark
export PATH=/Users/clarencestark/code/open-webui/.venv/bin:/Users/clarencestark/.nvm/versions/node/v22.12.0/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
export PYTHONUNBUFFERED=1
export HOST=127.0.0.1
export PORT=8080
export UVICORN_WORKERS=4

exec /bin/zsh -lic '
export HOME=/Users/clarencestark
export PATH=/Users/clarencestark/code/open-webui/.venv/bin:/Users/clarencestark/.nvm/versions/node/v22.12.0/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
export PYTHONUNBUFFERED=1
export HOST=127.0.0.1
export PORT=8080
export UVICORN_WORKERS=4
cd /Users/clarencestark/code/open-webui/backend
exec ./start.sh
'
