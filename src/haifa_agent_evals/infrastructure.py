from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from harbor.publisher.packager import Packager

from haifa_agent_evals.runner import _child_environment, new_run_id

INFRA_EVIDENCE_ENV = "HAIFA_EVAL_INFRA_EVIDENCE"
CONTAINER_PROXY_ENV = "HAIFA_EVALS_CONTAINER_PROXY"
PREFLIGHT_TARGET_ENV = "HAIFA_EVALS_PREFLIGHT_TARGET_URL"
DEFAULT_MAX_AGE_MINUTES = 30


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_compose_overlay() -> Path:
    return repository_root() / "infra" / "harbor-compose-proxy.yaml"


def default_preflight_tasks() -> Path:
    return repository_root() / "infra" / "preflight" / "tasks"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_proxy_endpoint(proxy_url: str) -> dict[str, object]:
    parsed = urlsplit(proxy_url)
    if parsed.scheme not in {"http", "https", "socks5", "socks5h"}:
        raise ValueError("proxy URL must use http, https, socks5, or socks5h")
    if parsed.username or parsed.password:
        raise ValueError("proxy URL credentials are not supported in evaluation evidence")
    if not parsed.hostname or parsed.port is None:
        raise ValueError("proxy URL must include an explicit host and port")
    return {"scheme": parsed.scheme, "host": parsed.hostname, "port": parsed.port}


def _target_host(target_url: str) -> str:
    parsed = urlsplit(target_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("preflight target must be an HTTP(S) URL")
    return parsed.hostname


def _command_name(command: str) -> str:
    return Path(command).stem.lower()


def _job_config(job_name: str, jobs_dir: Path, tasks_path: Path, overlay: Path) -> dict:
    return {
        "job_name": job_name,
        "jobs_dir": str(jobs_dir.resolve()),
        "n_attempts": 1,
        "n_concurrent_trials": 1,
        "quiet": False,
        "retry": {"max_retries": 0},
        "environment": {
            "type": "docker",
            "delete": True,
            "extra_docker_compose": [str(overlay.resolve())],
        },
        "verifier": {"override_timeout_sec": 60, "max_timeout_sec": 60},
        "agents": [
            {
                "name": "oracle",
                "model_name": "infrastructure-preflight",
                "override_timeout_sec": 60,
                "max_timeout_sec": 60,
                "override_setup_timeout_sec": 60,
            }
        ],
        "datasets": [{"path": str(tasks_path.resolve()), "task_names": ["harbor-compose-network"]}],
    }


def run_compose_network_preflight(
    output: Path,
    *,
    proxy_url: str,
    target_url: str = "https://api.deepseek.com/",
    overlay: Path | None = None,
    work_dir: Path | None = None,
    container_cli: str = "podman",
    environment: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Run a no-model probe inside Harbor's real Compose-managed main service."""

    safe_proxy = _safe_proxy_endpoint(proxy_url)
    target_host = _target_host(target_url)
    resolved_overlay = (overlay or default_compose_overlay()).resolve()
    if not resolved_overlay.is_file():
        raise ValueError("Harbor Compose overlay does not exist")
    tasks_path = default_preflight_tasks().resolve()
    task_path = tasks_path / "harbor-compose-network"
    if not (task_path / "task.toml").is_file():
        raise ValueError("Harbor Compose preflight task is missing")

    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    resolved_work_dir = work_dir or (
        repository_root()
        / "work"
        / "runs"
        / "preflight"
        / "harbor"
        / f"compose-network-{new_run_id()}"
    )
    if resolved_work_dir.exists():
        raise ValueError("infrastructure preflight work directory already exists")
    resolved_work_dir.parent.mkdir(parents=True, exist_ok=True)
    job_name = resolved_work_dir.name
    job_config_path = resolved_work_dir.parent / f"{job_name}-harbor-job.yaml"
    if job_config_path.exists():
        raise ValueError("infrastructure preflight control file already exists")
    job_config_path.write_text(
        yaml.safe_dump(
            _job_config(job_name, resolved_work_dir.parent, tasks_path, resolved_overlay),
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    child_environment = _child_environment(resolved_work_dir, container_cli)
    if environment is not None:
        child_environment.update(environment)
    child_environment[CONTAINER_PROXY_ENV] = proxy_url
    child_environment[PREFLIGHT_TARGET_ENV] = target_url
    completed = subprocess.run(  # noqa: S603
        ["harbor", "run", "--config", str(job_config_path), "--yes"],
        check=False,
        env=child_environment,
    )

    trial_results = [
        path for path in resolved_work_dir.glob("*/result.json") if path.parent != resolved_work_dir
    ]
    trial: dict[str, object] = {}
    if len(trial_results) == 1:
        try:
            trial = json.loads(trial_results[0].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            trial = {}
    verifier = trial.get("verifier_result") if isinstance(trial, dict) else None
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    passed = (
        completed.returncode == 0
        and len(trial_results) == 1
        and reward == 1.0
        and trial.get("exception_info") is None
    )
    evidence: dict[str, object] = {
        "schemaVersion": 1,
        "status": "READY" if passed else "BLOCKED",
        "checkedAt": timestamp.isoformat(),
        "expiresAt": (timestamp + timedelta(minutes=DEFAULT_MAX_AGE_MINUTES)).isoformat(),
        "containerBackend": _command_name(container_cli),
        "networkProbe": {
            "kind": "harbor-compose-task",
            "composeNetworkVerified": passed,
            "targetHost": target_host,
            "proxyEndpoint": safe_proxy,
            "overlaySha256": _sha256(resolved_overlay),
            "taskSha256": Packager.compute_content_hash(task_path)[0],
            "harborExitCode": completed.returncode,
            "trialCount": len(trial_results),
            "reward": reward,
            "exception": trial.get("exception_info") is not None if trial else None,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return evidence


def validate_infrastructure_evidence(
    path: Path,
    *,
    overlay: Path,
    proxy_url: str,
    container_cli: str,
    now: datetime | None = None,
) -> tuple[bool, str, str | None]:
    if not path.is_file():
        return False, "Harbor Compose network preflight evidence is missing", None
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "Harbor Compose network preflight evidence is unreadable", None
    if not isinstance(evidence, dict) or evidence.get("status") != "READY":
        return False, "Harbor Compose network preflight is not READY", None
    probe = evidence.get("networkProbe")
    if not isinstance(probe, dict) or probe.get("composeNetworkVerified") is not True:
        return False, "evidence did not verify the Harbor Compose network", None
    if not overlay.is_file() or probe.get("overlaySha256") != _sha256(overlay):
        return False, "Compose overlay does not match preflight evidence", None
    try:
        if probe.get("proxyEndpoint") != _safe_proxy_endpoint(proxy_url):
            return False, "proxy endpoint does not match preflight evidence", None
        expires_at = datetime.fromisoformat(str(evidence["expiresAt"]))
    except (KeyError, TypeError, ValueError):
        return False, "preflight identity or expiry is invalid", None
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if expires_at.astimezone(UTC) < current:
        return False, "Harbor Compose network preflight evidence has expired", None
    if evidence.get("containerBackend") != _command_name(container_cli):
        return False, "container backend does not match preflight evidence", None
    return True, "fresh Harbor Compose network preflight matched", _sha256(path)
