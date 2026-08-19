# Haifa Agent Evals

Small, independent coding-agent evaluation runner. Harbor owns task execution and verification; this repository validates one evaluation config, launches Harbor jobs, collects a flat CSV, and renders a Markdown comparison.

## Requirements

- Python 3.12
- `uv`
- Docker for real Harbor trials
- Java 21 and a Haifa CLI shaded JAR for the Haifa candidate

## Commands

```bash
uv sync --frozen
uv run evals run --config evals/coding-smoke-v1.yaml
uv run evals collect --job-dir work/coding-smoke-v1 --output reports/coding-smoke-v1/results.csv
uv run evals report --results reports/coding-smoke-v1/results.csv
uv run pytest
```

The checked-in smoke config is completed with exact Harbor dataset/task identities during Phase 2. Runtime artifacts under `work/` and generated reports are intentionally ignored.

See [`docs/01-haifa-agent-evals-architecture.md`](docs/01-haifa-agent-evals-architecture.md) and [`docs/05-m0-implementation-and-acceptance-plan.md`](docs/05-m0-implementation-and-acceptance-plan.md).
