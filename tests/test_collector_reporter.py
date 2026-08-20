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
    model: str = "provider/model",
    agent_version: str = "1.2.3",
    aider_log_version: str = "",
    aider_log_prefix_lines: int = 0,
) -> None:
    trial = job_dir / name
    trial.mkdir(parents=True)
    (trial / "config.json").write_text(
        json.dumps(
            {
                "task": {"name": task, "metadata": {"language": "python"}},
                "agent": {"name": candidate, "model_name": model},
                "attempt": 1,
            }
        ),
        encoding="utf-8",
    )
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": task,
                "agent_info": {"name": candidate, "version": agent_version},
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
    if aider_log_version:
        agent_dir = trial / "agent"
        agent_dir.mkdir()
        (agent_dir / "aider.txt").write_text(
            ("compose notice\n" * aider_log_prefix_lines)
            + f"{aider_log_version}\nadditional output that must not be collected\n",
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
    assert rows[0]["model"] == "provider/model"
    assert rows[0]["agent_version"] == "1.2.3"

    comparison = report(output)
    text = comparison.read_text(encoding="utf-8")
    assert "- Evaluation: `coding-smoke-v1`" in text
    assert "haifa/coding-smoke-v1-cpp-verifier-fixed@sha256:" in text
    assert "- Harbor: `0.20.0`" in text
    assert "- Model cost: `unavailable`" in text
    assert "complete candidate systems" in text
    assert (
        "| haifa | provider/model | 1.2.3 | 2 | 2 | 1 | 50.0% | 0 | 0 | 15.00s |"
        in text
    )
    assert (
        "| aider | provider/model | 1.2.3 | 1 | 0 | 0 | unavailable | 1 | 1 | "
        "30.00s |"
        in text
    )
    assert "Verifier reward 0.0" in text
    assert "Verifier did not return a trusted reward" not in text
    assert "## Agent exceptions" in text
    assert "| aider | task-a | ERROR | 0 | EnvironmentError |" in text
    assert "## Manual review and test focus" in text
    assert "both candidates were observed running as root" in text
    assert "ignored raw logs" in text
    assert "not a leaderboard" in text
    assert str(tmp_path) not in text


def test_report_shows_exception_even_when_verifier_passes(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    _trial(
        job_dir,
        "trial-1",
        "haifa",
        "task-a",
        1.0,
        10,
        "NonZeroAgentExitCodeError",
    )
    output = tmp_path / "results.csv"
    collect(job_dir, output, "coding-smoke-v1")

    text = report(output).read_text(encoding="utf-8")

    assert "| haifa | provider/model | 1.2.3 | 1 | 1 | 1 | 100.0% | 0 | 1 | " in text
    assert "| haifa | task-a | PASS | 0 | NonZeroAgentExitCodeError |" in text


def test_collect_recognizes_derived_cpp_task_language(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    _trial(
        job_dir,
        "trial-1",
        "haifa",
        "haifa/coding-smoke-cpp-gigasecond",
        1.0,
        10,
    )

    results = collect(job_dir, tmp_path / "results.csv")

    assert results[0].language == "cpp"


def test_collect_replaces_polluted_aider_version_with_safe_log_version(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    _trial(
        job_dir,
        "trial-1",
        "aider",
        "aider/polyglot_python_hangman",
        1.0,
        10,
        agent_version=r'compose provider "C:\\Users\\example\\docker-compose.exe"',
        aider_log_version="Aider v0.86.2",
        aider_log_prefix_lines=20,
    )

    results = collect(job_dir, tmp_path / "results.csv")

    assert results[0].agent_version == "0.86.2"
    assert "Users" not in (tmp_path / "results.csv").read_text(encoding="utf-8")


def test_wrappers_forward_unix_style_arguments() -> None:
    root = Path(__file__).parents[1]
    assert "uv run evals @args" in (root / "scripts" / "evals.ps1").read_text(encoding="utf-8")
    assert 'exec uv run evals "$@"' in (root / "scripts" / "evals.sh").read_text(encoding="utf-8")
