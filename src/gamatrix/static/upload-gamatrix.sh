#!/usr/bin/env bash
# Upload your GOG Galaxy database to gamatrix, unattended (issue #129).
#
# Copies the live Galaxy DB, asks gamatrix for a presigned S3 POST using your
# API token, and uploads straight to S3 — gamatrix ingests it from there. Run it
# from cron.
#
# Configure via environment variables (or edit the defaults below):
#   GAMATRIX_TOKEN     your API token        (or put it in ~/.gamatrix-token, chmod 600)
#   GAMATRIX_BASE_URL  https://gamatrix.example.com
#   GAMATRIX_DB_PATH   path to galaxy-2.0.db
#
# Requires: bash, curl, and python3 (for parsing the presign JSON), all on the
# PATH. curl ships on macOS/Linux; python3 does NOT ship on stock macOS, some
# Linux distros, or many WSL installs — install it first if `python3 --version`
# fails. (Written for bash 3.2, the version Apple still ships, so no mapfile.)
set -euo pipefail

BASE_URL="${GAMATRIX_BASE_URL:-https://gamatrix.example.com}"
TOKEN="${GAMATRIX_TOKEN:-}"
# Default macOS GOG Galaxy location; override with GAMATRIX_DB_PATH on Linux.
DB_PATH="${GAMATRIX_DB_PATH:-$HOME/Library/Application Support/GOG.com/Galaxy/storage/galaxy-2.0.db}"

if [[ -z "$TOKEN" && -r "$HOME/.gamatrix-token" ]]; then
  TOKEN="$(tr -d '[:space:]' < "$HOME/.gamatrix-token")"
fi
if [[ -z "$TOKEN" ]]; then
  echo "No API token. Set GAMATRIX_TOKEN or save it to ~/.gamatrix-token (chmod 600)." >&2
  exit 1
fi
if [[ ! -f "$DB_PATH" ]]; then
  echo "GOG Galaxy DB not found at: $DB_PATH (set GAMATRIX_DB_PATH)." >&2
  exit 1
fi

tmp="$(mktemp -t gamatrix-galaxy.XXXXXX.db)"
trap 'rm -f "$tmp"' EXIT
# Galaxy holds the DB open; copying it is fine for a read.
cp "$DB_PATH" "$tmp"

# 1) Presign (the only authenticated call).
presign="$(curl --fail --silent --show-error \
  -H "Authorization: Bearer ${TOKEN}" \
  "${BASE_URL%/}/upload/presign")"

url="$(printf '%s' "$presign" | python3 -c 'import sys,json; print(json.load(sys.stdin)["url"])')"

# 2) Build -F args from the presign fields (file LAST; S3 ignores anything after it).
# A while-read loop instead of mapfile, so this runs on bash 3.2 (stock macOS).
args=()
while IFS= read -r field; do
  args+=(-F "$field")
done < <(printf '%s' "$presign" | python3 -c '
import sys, json
for k, v in json.load(sys.stdin)["fields"].items():
    print(f"{k}={v}")')
args+=(-F "file=@${tmp}")

# 3) Upload straight to S3.
curl --fail --silent --show-error "${args[@]}" "$url"
echo "Uploaded $(du -h "$tmp" | cut -f1). gamatrix will ingest it shortly."
