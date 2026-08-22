#!/usr/bin/env bash
set -Eeuo pipefail

reward=/logs/verifier/reward.txt
mkdir -p "$(dirname "$reward")"
if test "${HAIFA_EVALS_COMPOSE_NETWORK_PREFLIGHT:-}" = "v1" \
  && test -s /tmp/haifa-evals-compose-network-preflight.ok \
  && grep -qx 'proxy=reachable' /tmp/haifa-evals-compose-network-preflight.ok; then
  echo 1 > "$reward"
else
  echo 0 > "$reward"
fi
