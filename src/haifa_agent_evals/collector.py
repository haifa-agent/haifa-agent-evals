from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CSV_FIELDS = (
    "eval_id",
    "candidate",
    "task_id",
    "language",
    "attempt",
    "status",
    "reward",
    "duration_seconds",
    "exit_code",
    "error_type",
    "trial_path",
)


@dataclass(frozen=True)
class Result:
    eval_id: str
    candidate: str
    task_id: str
    language: str
    attempt: int
    status: str
    reward: float | None
    duration_seconds: float | None
    exit_code: int | None
    error_type: str
    trial_path: str

    def as_row(self) -> dict[str, str | int | float]:
        return {
            "eval_id": self.eval_id,
            "candidate": self.candidate,
            "task_id": self.task_id,
            "language": self.language,
            "attempt": self.attempt,
            "status": self.status,
            "reward": "" if self.reward is None else self.reward,
            "duration_seconds": "" if self.duration_seconds is None else self.duration_seconds,
            "exit_code": "" if self.exit_code is None else self.exit_code,
            "error_type": self.error_type,
            "trial_path": self.trial_path,
        }


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value


def _nested(value: dict[str, Any], *path: str) -> Any:
    current: Any = value
    for item in path:
        if not isinstance(current, dict):
            return None
        current = current.get(item)
    return current


def _first(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _trial_result(job_dir: Path, trial_dir: Path, eval_id: str) -> Result:
    config = _read_json(trial_dir / "config.json")
    raw = _read_json(trial_dir / "result.json")
    reward = _float_or_none(_first(raw.get("reward"), _nested(raw, "verifier_result", "reward")))
    error_type = str(
        _first(
            raw.get("error_type"),
            _nested(raw, "exception_info", "exception_type"),
            "",
        )
    )
    status = "ERROR" if reward is None else ("PASS" if reward >= 1.0 else "FAIL")
    task_id = str(_first(raw.get("task_name"), _nested(config, "task", "name"), "unknown"))
    candidate = str(_first(raw.get("candidate"), _nested(config, "agent", "name"), "unknown"))
    duration = _float_or_none(
        _first(raw.get("duration_seconds"), _nested(raw, "timing", "duration_seconds"))
    )
    exit_code = _int_or_none(
        _first(raw.get("exit_code"), _nested(raw, "agent_result", "exit_code"))
    )
    language = str(
        _first(raw.get("language"), _nested(config, "task", "metadata", "language"), "unknown")
    )
    attempt = int(_first(raw.get("attempt"), config.get("attempt"), 1))
    return Result(
        eval_id=eval_id,
        candidate=candidate,
        task_id=task_id,
        language=language,
        attempt=attempt,
        status=status,
        reward=reward,
        duration_seconds=duration,
        exit_code=exit_code,
        error_type=error_type,
        trial_path=trial_dir.relative_to(job_dir).as_posix(),
    )


def collect(job_dir: Path, output: Path, eval_id: str | None = None) -> list[Result]:
    if not job_dir.is_dir():
        raise ValueError(f"job directory does not exist: {job_dir}")
    resolved_eval_id = eval_id or job_dir.name
    trial_dirs = sorted(
        result_path.parent
        for result_path in job_dir.rglob("result.json")
        if result_path.parent != job_dir and (result_path.parent / "config.json").is_file()
    )
    if not trial_dirs:
        raise ValueError("no Harbor trial result.json files found")
    results = [_trial_result(job_dir, trial_dir, resolved_eval_id) for trial_dir in trial_dirs]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(result.as_row() for result in results)
    return results
