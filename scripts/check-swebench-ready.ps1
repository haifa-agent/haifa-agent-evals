#!/usr/bin/env pwsh

$ErrorActionPreference = 'Stop'
uv run python -m haifa_agent_evals.readiness @args
exit $LASTEXITCODE
