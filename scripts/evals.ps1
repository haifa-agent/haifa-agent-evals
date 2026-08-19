#!/usr/bin/env pwsh

$ErrorActionPreference = 'Stop'
uv run evals @args
exit $LASTEXITCODE
