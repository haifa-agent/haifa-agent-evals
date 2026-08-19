# Haifa Agent Evals

Small, independent coding-agent evaluation runner. Harbor owns task execution and verification; this repository validates one evaluation config, launches Harbor jobs, collects a flat CSV, and renders a Markdown comparison.

## Requirements

- Python 3.12
- `uv`
- Docker, or a Docker-compatible Podman setup, for real Harbor trials
- A current Haifa CLI shaded JAR for the Haifa candidate

## Commands

```bash
uv sync --frozen
uv run evals run --config evals/coding-smoke-v1.yaml
uv run evals collect --job-dir work/coding-smoke-v1 --output reports/coding-smoke-v1/results.csv
uv run evals report --results reports/coding-smoke-v1/results.csv
uv run pytest
```

The checked-in smoke config freezes Harbor 0.20.0, the Aider Polyglot dataset digest, six task IDs, and both model routes. `run` uses the Harbor registry by default. Set `HAIFA_EVAL_TASKS_PATH` to an exported directory containing all six configured tasks to use a local verified cache. Set `HAIFA_EVAL_JAR_PATH` to override the default sibling Haifa JAR. An optional pinned Temurin archive can be supplied as `HAIFA_EVAL_JRE_PATH`; otherwise the adapter downloads and verifies that JRE when Java 21 is absent.

Runtime artifacts under `work/` and generated reports are intentionally ignored.

See [`docs/01-haifa-agent-evals-architecture.md`](docs/01-haifa-agent-evals-architecture.md) and [`docs/05-m0-implementation-and-acceptance-plan.md`](docs/05-m0-implementation-and-acceptance-plan.md).
