import csv
import hashlib
import json
import sqlite3
from pathlib import Path

from haifa_agent_evals.config import Candidate, EvaluationConfig
from haifa_agent_evals.evidence import inspect_haifa_evidence
from haifa_agent_evals.finalizer import finalize
from haifa_agent_evals.runner import config_sha256


def _write_evidence(
    trial: Path,
    *,
    outcome_unknown: bool = False,
    failure_code: str = "",
) -> None:
    agent = trial / "agent"
    transcripts = agent / "haifa-transcripts"
    transcripts.mkdir(parents=True)
    database = agent / "haifa-runtime.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE run (
            run_id TEXT,
            status TEXT,
            parent_run_id TEXT,
            usage_model_calls INTEGER
        );
        CREATE TABLE step (step_id TEXT);
        CREATE TABLE tool_call (status TEXT);
        CREATE TABLE tool_journal (state TEXT);
        CREATE TABLE runtime_event (event_id TEXT);
        INSERT INTO run VALUES ('run-1', 'COMPLETED', NULL, 2);
        """
    )
    if outcome_unknown:
        connection.execute("INSERT INTO tool_journal VALUES ('OUTCOME_UNKNOWN')")
    connection.commit()
    connection.close()

    trace = [
        {
            "runId": "run-1",
            "toolCallId": "tool-1",
            "operation": "tool.execute",
            "attributes": {"toolName": "file.write"},
        },
        {
            "runId": "run-1",
            "toolCallId": "tool-1",
            "operation": "tool.persisted",
            "attributes": {"successful": True},
        },
    ]
    if failure_code:
        trace.append(
            {
                "runId": "run-1",
                "operation": "runtime.error",
                "attributes": {"errorCode": failure_code},
            }
        )
    (agent / "haifa-trace.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in trace), encoding="utf-8"
    )
    transcript = [
        {"runId": "run-1", "sequence": 1, "eventType": "run.created"},
        {"runId": "run-1", "sequence": 2, "eventType": "run.completed"},
    ]
    (transcripts / "run-1.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in transcript), encoding="utf-8"
    )


def _trial(job_dir: Path) -> Path:
    trial = job_dir / "trial-1"
    trial.mkdir(parents=True)
    (trial / "config.json").write_text(
        json.dumps(
            {
                "task": {"name": "org/task-a", "metadata": {"language": "python"}},
                "agent": {"name": "haifa", "model_name": "provider/model"},
                "attempt": 1,
            }
        ),
        encoding="utf-8",
    )
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": "org/task-a",
                "agent_info": {"name": "haifa", "version": "1.2.3"},
                "verifier_result": {"rewards": {"reward": 1.0}},
                "started_at": "2026-08-21T00:00:00+00:00",
                "finished_at": "2026-08-21T00:00:10+00:00",
                "agent_result": {"metadata": {"exit_code": 0}},
                "exception_info": None,
            }
        ),
        encoding="utf-8",
    )
    _write_evidence(trial)
    return trial


def _config_file(tmp_path: Path) -> tuple[Path, EvaluationConfig]:
    path = tmp_path / "eval.yaml"
    path.write_text(
        """\
id: smoke
dataset: org/data@v1
tasks: [org/task-a]
attempts: 1
timeoutMinutes: 20
candidates:
  - id: haifa
    agent: package:Haifa
    model: provider/model
""",
        encoding="utf-8",
    )
    return path, EvaluationConfig(
        id="smoke",
        dataset="org/data@v1",
        tasks=("org/task-a",),
        attempts=1,
        timeout_minutes=20,
        candidates=(Candidate("haifa", "package:Haifa", "provider/model"),),
    )


def _write_run_manifest(tmp_path: Path, config: EvaluationConfig) -> Path:
    path = tmp_path / "run-1-run-manifest.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "runId": "run-1",
                "evalId": config.id,
                "configSha256": config_sha256(config),
                "dataset": config.dataset,
                "plannedTrials": [{"candidate": "haifa", "taskId": "org/task-a", "attempt": 1}],
                "runStatus": "HARBOR_FINISHED",
                "admissionSha256": None,
                "preflightSha256": None,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_inspects_sqlite_trace_and_transcript_without_reading_payloads(tmp_path: Path) -> None:
    trial = tmp_path / "trial"
    _write_evidence(trial, outcome_unknown=True, failure_code="TOOL_OUTCOME_UNKNOWN")

    result = inspect_haifa_evidence(trial)

    assert result.valid
    assert result.run_id == "run-1"
    assert result.model_attempts == 2
    assert result.workspace_changed is True
    assert result.tool_outcome_unknown is True
    assert result.failure_stage == "tool"
    assert result.failure_code == "TOOL_OUTCOME_UNKNOWN"


def test_missing_transcript_is_explicitly_invalid(tmp_path: Path) -> None:
    trial = tmp_path / "trial"
    _write_evidence(trial)
    (trial / "agent" / "haifa-transcripts" / "run-1.jsonl").unlink()

    result = inspect_haifa_evidence(trial)

    assert not result.valid
    assert "Haifa transcript JSONL is missing" in result.issues


def test_execution_run_makes_workspace_change_unknown_without_direct_evidence(
    tmp_path: Path,
) -> None:
    trial = tmp_path / "trial"
    _write_evidence(trial)
    trace_path = trial / "agent" / "haifa-trace.jsonl"
    trace_path.write_text(
        trace_path.read_text(encoding="utf-8").replace("file.write", "execution.run"),
        encoding="utf-8",
    )

    result = inspect_haifa_evidence(trial)

    assert result.valid
    assert result.workspace_changed is None


def test_finalize_archives_a_complete_run_and_writes_checksums(tmp_path: Path, monkeypatch) -> None:
    config_path, config = _config_file(tmp_path)
    dataset_manifest = tmp_path / "dataset.toml"
    dataset_manifest.write_text("[dataset]\nname = 'org/data'\n", encoding="utf-8")
    monkeypatch.setenv("HAIFA_EVAL_DATASET_MANIFEST_PATH", str(dataset_manifest))
    job_dir = tmp_path / "run-1"
    _trial(job_dir)
    _write_run_manifest(tmp_path, config)
    archive = tmp_path / "archive"

    result = finalize(config_path, job_dir, archive)

    assert result["status"] == "COMPLETE"
    assert result["observedTrials"] == 1
    assert (archive / "jobs" / "harbor-job" / "trial-1" / "agent" / "haifa-runtime.db").is_file()
    assert (archive / "reports" / "comparison.md").is_file()
    assert (archive / "integrity" / "finalization.json").is_file()
    checksums = (archive / "integrity" / "SHA256SUMS.txt").read_text(encoding="utf-8")
    assert "reports/results.csv" in checksums
    with (archive / "reports" / "results.csv").open(encoding="utf-8", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["model_attempts"] == "2"
    assert row["workspace_changed"] == "true"


def test_finalize_blocks_incomplete_trajectory_but_keeps_diagnostics(
    tmp_path: Path, monkeypatch
) -> None:
    config_path, config = _config_file(tmp_path)
    dataset_manifest = tmp_path / "dataset.toml"
    dataset_manifest.write_text("[dataset]\nname = 'org/data'\n", encoding="utf-8")
    monkeypatch.setenv("HAIFA_EVAL_DATASET_MANIFEST_PATH", str(dataset_manifest))
    job_dir = tmp_path / "run-1"
    trial = _trial(job_dir)
    (trial / "agent" / "haifa-trace.jsonl").unlink()
    _write_run_manifest(tmp_path, config)

    result = finalize(config_path, job_dir, tmp_path / "archive")

    assert result["status"] == "BLOCKED"
    assert result["invalidTrials"] == 1
    assert result["evidenceIssues"] > 0
    assert (tmp_path / "archive" / "reports" / "comparison.md").is_file()


def test_finalize_blocks_a_secret_finding_without_copying_the_value_to_summary(
    tmp_path: Path, monkeypatch
) -> None:
    config_path, config = _config_file(tmp_path)
    dataset_manifest = tmp_path / "dataset.toml"
    dataset_manifest.write_text("[dataset]\nname = 'org/data'\n", encoding="utf-8")
    monkeypatch.setenv("HAIFA_EVAL_DATASET_MANIFEST_PATH", str(dataset_manifest))
    job_dir = tmp_path / "run-1"
    trial = _trial(job_dir)
    secret = "sk-abcdefghijklmnop1234"
    credential = "abcdefghijklmnop123456"
    (trial / "agent" / "debug.log").write_text(secret, encoding="utf-8")
    (trial / "agent" / "config-leak.json").write_text(
        json.dumps({"DEEPSEEK_API_KEY": credential}), encoding="utf-8"
    )
    _write_run_manifest(tmp_path, config)

    result = finalize(config_path, job_dir, tmp_path / "archive")

    assert result["status"] == "BLOCKED"
    assert {finding["rule"] for finding in result["safetyFindings"]} >= {
        "credential-value",
        "secret-token",
    }
    finalization_text = (tmp_path / "archive" / "integrity" / "finalization.json").read_text(
        encoding="utf-8"
    )
    assert secret not in finalization_text
    assert credential not in finalization_text


def test_finalize_never_overwrites_an_archive(tmp_path: Path) -> None:
    config_path, _ = _config_file(tmp_path)
    job_dir = tmp_path / "run-1"
    job_dir.mkdir()
    archive = tmp_path / "archive"
    archive.mkdir()

    try:
        finalize(config_path, job_dir, archive)
    except ValueError as error:
        assert "never overwrites" in str(error)
    else:
        raise AssertionError("finalize should reject an existing archive")


def test_finalize_requires_the_preflight_evidence_frozen_by_the_run_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    config_path, config = _config_file(tmp_path)
    dataset_manifest = tmp_path / "dataset.toml"
    dataset_manifest.write_text("[dataset]\nname = 'org/data'\n", encoding="utf-8")
    monkeypatch.setenv("HAIFA_EVAL_DATASET_MANIFEST_PATH", str(dataset_manifest))
    job_dir = tmp_path / "run-1"
    _trial(job_dir)
    manifest_path = _write_run_manifest(tmp_path, config)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["preflightSha256"] = hashlib.sha256(b"expected").hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    try:
        finalize(
            config_path,
            job_dir,
            tmp_path / "archive",
            preflight_path=tmp_path / "missing-preflight.json",
        )
    except ValueError as error:
        assert "preflight evidence" in str(error)
    else:
        raise AssertionError("finalize should require frozen preflight evidence")
