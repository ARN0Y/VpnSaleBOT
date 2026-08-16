#!/usr/bin/env bash
# Restore a PasarGuard panel from an archive produced by the bot's backup job.
#
# The archives the bot ships are in the official PasarGuard backup layout, so the
# preferred path is the panel's own CLI:
#
#     pasarguard restore backup_YYYYMMDDHHMMSS.zip
#
# This script is the fallback for when the CLI is missing or broken. It performs
# the same procedure the CLI does, which is NOT a plain `psql < dump.sql`:
# TimescaleDB refuses a dump replayed without the pre/post restore wrapper, and
# the load must go straight to the database container, never through pgbouncer.
#
# Usage:
#   ./restore_pasarguard.sh <archive.zip> [--compose /opt/pasarguard/docker-compose.yml] [--yes]
#
# The archive may also be given as split parts; rebuild them first with:
#   cat backup_xxx.part*.zip > backup_xxx.zip

set -Eeuo pipefail

ARCHIVE=""
COMPOSE_FILE="/opt/pasarguard/docker-compose.yml"
ASSUME_YES=0

die() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[34m==>\033[0m %s\n' "$*"; }
ok() { printf '\033[32m✓\033[0m %s\n' "$*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --compose) COMPOSE_FILE="${2:-}"; shift 2 ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) ARCHIVE="$1"; shift ;;
  esac
done

[[ -n "$ARCHIVE" ]] || die "usage: $0 <archive.zip> [--compose FILE] [--yes]"
[[ -f "$ARCHIVE" ]] || die "archive not found: $ARCHIVE"
[[ -f "$COMPOSE_FILE" ]] || die "docker-compose.yml not found: $COMPOSE_FILE"
command -v docker >/dev/null || die "docker is not installed"
command -v unzip >/dev/null || die "unzip is not installed"

# Prefer the official CLI when it is present — it is the maintained path.
if [[ -x /usr/local/bin/pasarguard ]] && [[ "${SKIP_CLI:-0}" != "1" ]]; then
  info "PasarGuard CLI found — using the official restore"
  exec /usr/local/bin/pasarguard restore "$ARCHIVE"
fi

TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

info "Extracting $ARCHIVE"
unzip -q -o "$ARCHIVE" -d "$TMP" || die "could not extract the archive (split parts not rebuilt?)"

DUMP="$(find "$TMP" -maxdepth 2 -name 'db_backup.sql' -print -quit)"
ENV_FILE="$(find "$TMP" -maxdepth 2 -name '.env' -print -quit)"
[[ -n "$DUMP" ]] || die "db_backup.sql not found in the archive — it is not restorable"
[[ -n "$ENV_FILE" ]] || die ".env not found in the archive — the database URL is unknown"
[[ -s "$DUMP" ]] || die "db_backup.sql is empty — the backup is unusable"

# Credentials come from the archived .env, so restoring onto a rebuilt server
# uses the values that backup was actually taken with.
DB_URL="$(grep -E '^\s*SQLALCHEMY_DATABASE_URL' "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '"'"'"' ' )"
[[ -n "$DB_URL" ]] || die "SQLALCHEMY_DATABASE_URL missing from the archived .env"
if [[ ! "$DB_URL" =~ ^[a-z+]+://([^:]+):([^@]*)@[^/]+/(.+)$ ]]; then
  die "could not parse SQLALCHEMY_DATABASE_URL"
fi
DB_USER="${BASH_REMATCH[1]}"
DB_PASS="${BASH_REMATCH[2]}"
DB_NAME="${BASH_REMATCH[3]%%\?*}"

PROJECT="$(basename "$(dirname "$COMPOSE_FILE")")"
CONTAINER="$(docker compose -f "$COMPOSE_FILE" -p "$PROJECT" ps -q timescaledb 2>/dev/null || true)"
[[ -n "$CONTAINER" ]] || CONTAINER="${PROJECT}-timescaledb-1"
docker inspect -f '{{.State.Running}}' "$CONTAINER" >/dev/null 2>&1 \
  || die "database container not running: $CONTAINER"

IS_TIMESCALE=0
grep -q 'image: timescale/timescaledb' "$COMPOSE_FILE" && IS_TIMESCALE=1

echo
printf '\033[33mThis REPLACES the current panel database.\033[0m\n'
echo "  archive   : $ARCHIVE"
echo "  dump size : $(du -h "$DUMP" | cut -f1)"
echo "  container : $CONTAINER"
echo "  database  : $DB_NAME (timescaledb=$IS_TIMESCALE)"
echo
if [[ "$ASSUME_YES" != "1" ]]; then
  read -r -p "Type 'restore' to continue: " answer
  [[ "$answer" == "restore" ]] || die "aborted"
fi

pex() { docker exec -e PGPASSWORD="$DB_PASS" "$CONTAINER" "$@"; }

info "Stopping the panel so nothing writes during the restore"
docker compose -f "$COMPOSE_FILE" -p "$PROJECT" stop pasarguard >/dev/null 2>&1 || true

info "Dropping and recreating $DB_NAME"
pex psql -U "$DB_USER" -d postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${DB_NAME}' AND pid<>pg_backend_pid();" >/dev/null
pex psql -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS \"${DB_NAME}\";" >/dev/null
pex psql -U "$DB_USER" -d postgres -c "CREATE DATABASE \"${DB_NAME}\" OWNER \"${DB_USER}\";" >/dev/null

if [[ "$IS_TIMESCALE" == "1" ]]; then
  info "Preparing TimescaleDB (pre_restore)"
  pex psql -U "$DB_USER" -d "$DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS timescaledb;" >/dev/null
  pex psql -U "$DB_USER" -d "$DB_NAME" -c "SELECT timescaledb_pre_restore();" >/dev/null
fi

info "Loading the dump (this can take several minutes)"
set +e
docker exec -i -e PGPASSWORD="$DB_PASS" "$CONTAINER" \
  psql -v ON_ERROR_STOP=1 -U "$DB_USER" --dbname="$DB_NAME" < "$DUMP" >"$TMP/restore.log" 2>&1
RC=$?
set -e

if [[ "$IS_TIMESCALE" == "1" ]]; then
  info "Finalising TimescaleDB (post_restore)"
  pex psql -U "$DB_USER" -d "$DB_NAME" -c "SELECT timescaledb_post_restore();" >/dev/null
fi

if [[ "$RC" != "0" ]]; then
  tail -30 "$TMP/restore.log" >&2
  cp "$TMP/restore.log" "./pasarguard_restore_failed.log" 2>/dev/null || true
  die "restore failed (see ./pasarguard_restore_failed.log)"
fi

# Certificates and subscription templates live outside the database.
DATA_DIR="$(find "$TMP" -maxdepth 2 -type d -name 'pasarguard_data' -print -quit)"
if [[ -n "$DATA_DIR" ]]; then
  info "Restoring certificates and templates"
  mkdir -p /var/lib/pasarguard
  cp -a "$DATA_DIR/." /var/lib/pasarguard/ 2>/dev/null || true
fi

info "Starting the panel"
docker compose -f "$COMPOSE_FILE" -p "$PROJECT" up -d >/dev/null

USERS="$(pex psql -U "$DB_USER" -d "$DB_NAME" -tAc 'SELECT COUNT(*) FROM users;' 2>/dev/null || echo '?')"
ok "Restore complete — users in the restored database: $USERS"
echo "Check the panel is healthy:  docker compose -f $COMPOSE_FILE -p $PROJECT ps"
