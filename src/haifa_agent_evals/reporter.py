from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from haifa_agent_evals.config import load_config


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("results CSV is empty")
    required = {
        "candidate",
        "eval_id",
        "model",
        "agent_version",
        "task_id",
        "status",
        "duration_seconds",
        "error_type",
        "exit_code",
        "reward",
    }
    missing = required - set(rows[0])
    if missing:
        raise ValueError(f"results CSV missing fields: {', '.join(sorted(missing))}")
    invalid = sorted({row["status"] for row in rows} - {"PASS", "FAIL", "ERROR"})
    if invalid:
        raise ValueError(f"invalid result statuses: {', '.join(invalid)}")
    diagnostic_defaults = {
        "trial_validity": "",
        "agent_clean_exit": "unknown",
        "workspace_changed": "unknown",
        "verifier_executed": "unknown",
        "failure_stage": "",
        "failure_code": "",
        "model_attempts": "",
        "tool_outcome_unknown": "unknown",
    }
    for row in rows:
        for field, default in diagnostic_defaults.items():
            row.setdefault(field, default)
        if not row["trial_validity"]:
            row["trial_validity"] = "VALID" if row["status"] != "ERROR" else "INCOMPLETE"
    return rows


def _duration(rows: list[dict[str, str]]) -> str:
    values = [float(row["duration_seconds"]) for row in rows if row["duration_seconds"]]
    return "unavailable" if not values else f"{statistics.median(values):.2f}s"


def _single_value(rows: list[dict[str, str]], field: str) -> str:
    values = sorted({row[field] for row in rows if row[field]})
    return values[0] if len(values) == 1 else "mixed"


def _evaluation_facts(rows: list[dict[str, str]]) -> tuple[str, str, str]:
    eval_id = _single_value(rows, "eval_id")
    repository = Path(__file__).resolve().parents[2]
    config_path = repository / "evals" / f"{eval_id}.yaml"
    dataset = load_config(config_path).dataset if config_path.is_file() else "unavailable"
    try:
        harbor_version = version("harbor")
    except PackageNotFoundError:
        harbor_version = "unavailable"
    return eval_id, dataset, harbor_version


def _failure_reason(row: dict[str, str]) -> str:
    if row["error_type"]:
        return row["error_type"]
    if row["status"] == "FAIL" and row["reward"]:
        return f"Verifier reward {row['reward']}"
    return "Verifier did not return a trusted reward"


def report(results_path: Path, output: Path | None = None) -> Path:
    rows = _read_rows(results_path)
    output_path = output or results_path.with_name("comparison.md")
    by_candidate: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_candidate[row["candidate"]].append(row)
    eval_id, dataset, harbor_version = _evaluation_facts(rows)

    lines = [
        "# Coding Agent Comparison",
        "",
        "## Run facts",
        "",
        f"- Evaluation: `{eval_id}`",
        f"- Dataset: `{dataset}`",
        f"- Harbor: `{harbor_version}`",
        "- Model cost: `unavailable` (Harbor did not emit reliable token/cost telemetry)",
        "- Comparison scope: complete candidate systems; model API routes/styles differ",
        "",
        "## Candidate summary",
        "",
        "| Candidate | Model route | Agent version | Planned | Valid | Passed | "
        "Pass rate | Invalid | Clean completion | Agent exceptions | Median duration |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for candidate in sorted(by_candidate):
        candidate_rows = by_candidate[candidate]
        valid = [row for row in candidate_rows if row["trial_validity"] == "VALID"]
        passed = sum(row["status"] == "PASS" for row in valid)
        rate = "unavailable" if not valid else f"{passed / len(valid):.1%}"
        invalid = sum(row["trial_validity"] != "VALID" for row in candidate_rows)
        clean_known = [
            row for row in candidate_rows if row["agent_clean_exit"] in {"true", "false"}
        ]
        clean = sum(row["agent_clean_exit"] == "true" for row in clean_known)
        clean_summary = "unavailable" if not clean_known else f"{clean}/{len(clean_known)}"
        agent_exceptions = sum(bool(row["error_type"]) for row in candidate_rows)
        lines.append(
            f"| {candidate} | {_single_value(candidate_rows, 'model')} | "
            f"{_single_value(candidate_rows, 'agent_version')} | {len(candidate_rows)} | "
            f"{len(valid)} | {passed} | {rate} | {invalid} | {clean_summary} | "
            f"{agent_exceptions} | {_duration(candidate_rows)} |"
        )

    validity_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        validity_counts[row["trial_validity"]] += 1
    lines.extend(
        [
            "",
            "## Data quality",
            "",
            "Official PASS/FAIL/ERROR is preserved from Harbor reward. Trial validity and "
            "clean completion are diagnostic dimensions and never rewrite that score.",
            "",
            "| Trial validity | Count |",
            "| --- | ---: |",
        ]
    )
    for validity in sorted(validity_counts):
        lines.append(f"| {validity} | {validity_counts[validity]} |")

    candidates = sorted(by_candidate)
    tasks = sorted({row["task_id"] for row in rows})
    matrix = {(row["task_id"], row["candidate"]): row["status"] for row in rows}
    lines.extend(
        [
            "",
            "## Task matrix",
            "",
            f"| Task | {' | '.join(candidates)} |",
            f"| --- | {' | '.join('---' for _ in candidates)} |",
        ]
    )
    for task in tasks:
        statuses = [matrix.get((task, candidate), "MISSING") for candidate in candidates]
        lines.append(f"| {task} | {' | '.join(statuses)} |")

    failures = [row for row in rows if row["status"] != "PASS"]
    lines.extend(
        [
            "",
            "## Failures and errors",
            "",
            "| Candidate | Task | Status | Validity | Stage | Exit code | Reason |",
            "| --- | --- | --- | --- | --- | ---: | --- |",
        ]
    )
    if failures:
        for row in failures:
            lines.append(
                f"| {row['candidate']} | {row['task_id']} | {row['status']} | "
                f"{row['trial_validity']} | {row['failure_stage'] or '-'} | "
                f"{row['exit_code'] or '-'} | {_failure_reason(row)} |"
            )
    else:
        lines.append("| - | - | - | - | - | - | - |")

    agent_exceptions = [row for row in rows if row["error_type"]]
    lines.extend(
        [
            "",
            "## Agent exceptions",
            "",
            "Verifier reward remains the correctness source. This section separately "
            "shows trials where the agent process still exited with an exception.",
            "",
            "| Candidate | Task | Verifier status | Exit code | Exception |",
            "| --- | --- | --- | ---: | --- |",
        ]
    )
    if agent_exceptions:
        for row in agent_exceptions:
            lines.append(
                f"| {row['candidate']} | {row['task_id']} | {row['status']} | "
                f"{row['exit_code'] or '-'} | {row['error_type']} |"
            )
    else:
        lines.append("| - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## Manual review and test focus",
            "",
            "- Container identity: both candidates were observed running as root in these "
            "Harbor task images; verify the intended non-root contract separately.",
            "- Exit semantics: manually confirm that nonzero Haifa CLI exits never suppress "
            "the verifier and that reward remains the only correctness source.",
            "- Fairness: review the same-model claim together with the different API routes, "
            "Aider Playwright disablement, and identical network/time limits.",
            "- Dataset integrity: review the six oracle/no-op results and the narrowly scoped "
            "C++ verifier patch plus pinned task/dataset digests.",
            "- Result integrity: recompute CSV rewards, candidate summaries, and the task "
            "matrix from the raw Harbor trial results.",
            "- Isolation: verify hidden tests, benchmark answers, other candidate results, "
            "host credentials, and host source trees were not visible to candidates.",
            "- Evidence safety: inspect ignored raw logs separately and confirm published "
            "CSV, report, and safe traces contain no credentials, reasoning, provider "
            "responses, or host paths.",
            "- Cleanup and portability: repeat timeout cleanup and the external cache/proxy "
            "path on native Linux Docker; this run used Windows Podman compatibility layers.",
            "",
            "## Limitations",
            "",
            "This smoke comparison uses a small task set and one attempt per candidate/task. "
            "It validates the evaluation pipeline and this run only; it is not a leaderboard, "
            "does not establish statistical significance, and must not be generalized to all "
            "languages or large repositories.",
            "",
        ]
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path
