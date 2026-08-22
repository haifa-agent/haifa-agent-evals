#!/usr/bin/env bash
set -Eeuo pipefail

test "${HAIFA_EVALS_COMPOSE_NETWORK_PREFLIGHT:-}" = "v1"
proxy="${HAIFA_EVALS_CONTAINER_PROXY:?missing HAIFA_EVALS_CONTAINER_PROXY}"
target="${HAIFA_EVALS_PREFLIGHT_TARGET_URL:?missing HAIFA_EVALS_PREFLIGHT_TARGET_URL}"

status="$(curl --silent --show-error --location --max-time 20 \
  --proxy "$proxy" --noproxy "" --output /dev/null --write-out '%{http_code}' "$target")"
case "$status" in
  2??|3??|4??) ;;
  *)
    echo "unexpected HTTP status through proxy: $status" >&2
    exit 1
    ;;
esac

printf '%s\n' "compose=v1" "proxy=reachable" "http_status_class=${status%??}xx" \
  > /tmp/haifa-evals-compose-network-preflight.ok
