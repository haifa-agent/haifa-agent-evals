from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from pathlib import Path


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("results CSV is empty")
    required = {"candidate", "task_id", "status", "duration_seconds", "error_type"}
    missing = required - set(rows[0])
    if missing:
        raise ValueError(f"results CSV missing fields: {', '.join(sorted(missing))}")
    invalid = sorted({row["status"] for row in rows} - {"PASS", "FAIL", "ERROR"})
    if invalid:
        raise ValueError(f"invalid result statuses: {', '.join(invalid)}")
    return rows


def _duration(rows: list[dict[str, str]]) -> str:
    values = [float(row["duration_seconds"]) for row in rows if row["duration_seconds"]]
    return "unavailable" if not values else f"{statistics.median(values):.2f}s"


def report(results_path: Path, output: Path | None = None) -> Path:
    rows = _read_rows(results_path)
    output_path = output or results_path.with_name("comparison.md")
    by_candidate: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_candidate[row["candidate"]].append(row)

    lines = [
        "# Coding Agent Comparison",
        "",
        "## Candidate summary",
        "",
        "| Candidate | Planned | Valid | Passed | Pass rate | Errors | Median duration |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for candidate in sorted(by_candidate):
        candidate_rows = by_candidate[candidate]
        valid = [row for row in candidate_rows if row["status"] != "ERROR"]
        passed = sum(row["status"] == "PASS" for row in valid)
        rate = "unavailable" if not valid else f"{passed / len(valid):.1%}"
        errors = sum(row["status"] == "ERROR" for row in candidate_rows)
        lines.append(
            f"| {candidate} | {len(candidate_rows)} | {len(valid)} | {passed} | {rate} | "
            f"{errors} | {_duration(candidate_rows)} |"
        )

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
            "| Candidate | Task | Status | Error type |",
            "| --- | --- | --- | --- |",
        ]
    )
    if failures:
        for row in failures:
            lines.append(
                f"| {row['candidate']} | {row['task_id']} | {row['status']} | "
                f"{row['error_type'] or '-'} |"
            )
    else:
        lines.append("| - | - | - | - |")

    lines.extend(
        [
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
