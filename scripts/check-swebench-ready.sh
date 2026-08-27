#!/usr/bin/env sh
set -eu

exec uv run python -m haifa_agent_evals.readiness "$@"
