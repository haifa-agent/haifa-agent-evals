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

The checked-in smoke config freezes Harbor 0.20.0, six task IDs, both model routes, and the local derived dataset manifest in `evals/coding-smoke-v1.dataset.toml`. That manifest is based on the pinned Aider Polyglot dataset and changes only the selected C++ package name plus two premature verifier exits, using the tracked patch in `evals/patches/`. It is not published to the Harbor registry. `run` therefore requires the six exact task directories under `work/derived-tasks` by default and verifies every Harbor task digest plus the dataset digest before execution. Set `HAIFA_EVAL_TASKS_PATH` to use another complete cache; the same validation still applies.

Set `HAIFA_EVAL_JAR_PATH` to override the default sibling Haifa JAR. An optional pinned Temurin archive can be supplied as `HAIFA_EVAL_JRE_PATH`; otherwise the adapter downloads and verifies that JRE when Java 21 is absent.
Set `HAIFA_EVAL_EXTRA_DOCKER_COMPOSE` only when the local environment needs one explicit Harbor Compose overlay, such as a verified dependency-cache volume or proxy build arguments. The path must already exist; the Runner records it in the generated plan and job configuration.

Runtime artifacts under `work/` and generated reports are intentionally ignored.

See [`docs/01-haifa-agent-evals-architecture.md`](docs/01-haifa-agent-evals-architecture.md) and [`docs/05-m0-implementation-and-acceptance-plan.md`](docs/05-m0-implementation-and-acceptance-plan.md).
