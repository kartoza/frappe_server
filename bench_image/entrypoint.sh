#!/bin/bash
# Entrypoint for a single bench-unit container.
#
# Runs as root to fix volume ownership, then drops to the `frappe` user for
# everything bench-related. Idempotent: safe to run again on container
# restart without re-initializing an existing bench.
set -euo pipefail

FRAPPE_BRANCH="${FRAPPE_BRANCH:-version-15}"
MARIADB_HOST="${MARIADB_HOST:-mariadb}"
MARIADB_PORT="${MARIADB_PORT:-3306}"
REDIS_CACHE_HOST="${REDIS_CACHE_HOST:-redis-cache}"
REDIS_QUEUE_HOST="${REDIS_QUEUE_HOST:-redis-queue}"
REDIS_PORT="${REDIS_PORT:-6379}"

wait_for_tcp() {
  local host="$1"
  local port="$2"
  local label="$3"
  echo "Waiting for ${label} (${host}:${port})..."
  for _ in $(seq 1 60); do
    if (exec 3<>"/dev/tcp/${host}/${port}") 2>/dev/null; then
      exec 3>&- 3<&-
      echo "${label} is ready."
      return 0
    fi
    sleep 2
  done
  echo "Timed out waiting for ${label} at ${host}:${port}" >&2
  exit 1
}

wait_for_tcp "${MARIADB_HOST}" "${MARIADB_PORT}" "MariaDB"
wait_for_tcp "${REDIS_CACHE_HOST}" "${REDIS_PORT}" "Redis cache"
wait_for_tcp "${REDIS_QUEUE_HOST}" "${REDIS_PORT}" "Redis queue"

chown -R frappe:frappe /home/frappe

su frappe -s /bin/bash -c "
set -euo pipefail
cd /home/frappe

if [ ! -d frappe-bench/sites ]; then
  echo 'Initializing new bench on branch ${FRAPPE_BRANCH}...'
  bench init \
    --skip-redis-config-generation \
    --frappe-branch '${FRAPPE_BRANCH}' \
    --no-backups \
    frappe-bench
fi

cd frappe-bench
bench set-mariadb-host '${MARIADB_HOST}'
# The redis:// scheme is required here, not just host:port — Frappe's own
# Node.js socketio process (@redis/client) rejects a bare host:port with
# 'TypeError: Invalid protocol', even though the Python side tolerates it.
bench set-redis-cache-host 'redis://${REDIS_CACHE_HOST}:${REDIS_PORT}'
bench set-redis-queue-host 'redis://${REDIS_QUEUE_HOST}:${REDIS_PORT}'

# The webpack asset watcher is pure overhead inside a container.
sed -i '/^watch:/d' Procfile || true

exec bench start
"
