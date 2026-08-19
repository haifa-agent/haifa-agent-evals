import csv
import json
from pathlib import Path

from haifa_agent_evals.collector import collect
from haifa_agent_evals.reporter import report


def _trial(
    job_dir: Path,
    name: str,
    candidate: str,
    task: str,
    reward: float | None,
    duration: float,
    error_type: str = "",
) -> None:
    trial = job_dir / name
    trial.mkdir(parents=True)
    (trial / "config.json").write_text(
        json.dumps(
            {
                "task": {"name": task, "metadata": {"language": "python"}},
                "agent": {"name": candidate},
                "attempt": 1,
            }
        ),
        encoding="utf-8",
    )
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": task,
                "agent_info": {"name": candidate},
                "verifier_result": (None if reward is None else {"rewards": {"reward": reward}}),
                "started_at": "2026-08-19T00:00:00+00:00",
                "finished_at": f"2026-08-19T00:00:{int(duration):02d}+00:00",
                "agent_result": {"metadata": {"exit_code": 0}},
                "exception_info": (
                    None
                    if not error_type
                    else {"exception_type": error_type, "exception_message": "redacted"}
                ),
            }
        ),
        encoding="utf-8",
    )


def test_collect_and_report_keep_error_out_of_valid_denominator(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    _trial(job_dir, "trial-1", "haifa", "task-a", 1.0, 10)
    _trial(job_dir, "trial-2", "haifa", "task-b", 0.0, 20)
    _trial(job_dir, "trial-3", "aider", "task-a", None, 30, "EnvironmentError")
    output = tmp_path / "reports" / "results.csv"

    results = collect(job_dir, output, "coding-smoke-v1")

    assert [result.status for result in results] == ["PASS", "FAIL", "ERROR"]
    with output.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 3
    assert rows[0]["trial_path"] == "trial-1"

    comparison = report(output)
    text = comparison.read_text(encoding="utf-8")
    assert "| haifa | 2 | 2 | 1 | 50.0% | 0 | 15.00s |" in text
    assert "| aider | 1 | 0 | 0 | unavailable | 1 | 30.00s |" in text
    assert "not a leaderboard" in text
    assert str(tmp_path) not in text


def test_wrappers_forward_unix_style_arguments() -> None:
    root = Path(__file__).parents[1]
    assert "uv run evals @args" in (root / "scripts" / "evals.ps1").read_text(encoding="utf-8")
    assert 'exec uv run evals "$@"' in (root / "scripts" / "evals.sh").read_text(encoding="utf-8")
