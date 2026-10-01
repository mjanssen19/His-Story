#!/usr/bin/env bash
# Build the site and publish it + a copy of the database to your server.
# Settings come from deploy/deploy.env (not in git), see deploy/deploy.env.example.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/deploy.env
: "${HOST:?}" "${WEB_ROOT:?}" "${DB_DIR:?}" "${DATA:?}"

python3 -m his_story --data "$DATA" build

# Consistent copy of the database, even if it is open elsewhere.
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
sqlite3 "$DATA/his-story.sqlite" ".backup '$tmp/his-story.sqlite'"

rsync -az --delete "$DATA/site/" "$HOST:$WEB_ROOT/"
ssh "$HOST" "mkdir -p '$DB_DIR' && chmod 700 '$DB_DIR'"
rsync -az "$tmp/his-story.sqlite" "$HOST:$DB_DIR/his-story.sqlite"
echo "Published to $HOST:$WEB_ROOT (database: $DB_DIR)"
