from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from haifa_agent_evals.config import EvaluationConfig, load_config
from haifa_agent_evals.doctor import _provider_requirements, doctor
from haifa_agent_evals.infrastructure import (
    CONTAINER_PROXY_ENV,
    INFRA_EVIDENCE_ENV,
    default_compose_overlay,
    run_compose_network_preflight,
)
from haifa_agent_evals.proxy_relay import relay_status, start_relay
from haifa_agent_evals.runner import _default_haifa_jar, build_job_config

EXPECTED_JAVA_ARCHIVE_SHA256 = "f2dc5418092c43003db8f9005c4a286e1c0104fea96ccdd49e8ebd037cac9219"
DEFAULT_MINIMUM_FREE_GIB = 50.0


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_config_path() -> Path:
    return repository_root() / "evals" / "coding-swebench-verified-balanced-30-gpt56-terra-v1.yaml"


def default_tasks_path() -> Path:
    return (
        repository_root()
        / "work"
        / "cache"
        / "images"
        / "task-environments"
        / "coding-swebench-verified-balanced-30-gpt56-terra-v1-baseline-v3"
        / "tasks"
    )


def default_java_archive_path() -> Path:
    return (
        repository_root()
        / "work"
        / "cache"
        / "images"
        / "agent-infra"
        / "context"
        / "temurin-jdk.tar.gz"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check(name: str, passed: bool, detail: str) -> dict[str, str]:
    return {"name": name, "status": "PASS" if passed else "FAIL", "detail": detail}


def _java_archive_check(path: Path) -> dict[str, str]:
    if not path.is_file():
        return _check("offline-java-archive", False, "pinned Temurin JDK archive is missing")
    actual = _sha256(path)
    return _check(
        "offline-java-archive",
        actual == EXPECTED_JAVA_ARCHIVE_SHA256,
        (
            f"sha256:{actual}"
            if actual == EXPECTED_JAVA_ARCHIVE_SHA256
            else "pinned Temurin JDK archive digest does not match"
        ),
    )


def _task_set_check(config: EvaluationConfig, expected_task_count: int) -> dict[str, str]:
    actual = len(config.tasks)
    unique = len(set(config.tasks))
    passed = actual == expected_task_count and unique == actual
    return _check(
        "frozen-task-set",
        passed,
        f"{actual} configured tasks, {unique} unique; expected {expected_task_count}",
    )


def _plan_check(config: EvaluationConfig, tasks_path: Path) -> dict[str, str]:
    try:
        plan = build_job_config(
            config,
            repository_root() / "work" / "runs" / "readiness" / "plan-only",
            tasks_path,
            default_compose_overlay(),
        )
        task_names = plan["datasets"][0]["task_names"]  # type: ignore[index]
        candidates = plan["agents"]
        passed = len(task_names) == len(config.tasks) and len(candidates) == len(config.candidates)
        return _check(
            "harbor-plan",
            passed,
            f"{len(candidates)} candidate(s) x {len(task_names)} task(s) can be planned",
        )
    except (KeyError, TypeError, ValueError) as error:
        return _check("harbor-plan", False, str(error))


def verify_readiness(
    *,
    config_path: Path,
    tasks_path: Path,
    admission_path: Path,
    jar_path: Path,
    java_archive_path: Path,
    output_path: Path,
    doctor_output_path: Path,
    infrastructure_evidence_path: Path,
    container_cli: str,
    expected_task_count: int = 30,
    minimum_free_gib: float = DEFAULT_MINIMUM_FREE_GIB,
    source_proxy_host: str = "127.0.0.1",
    source_proxy_port: int = 2081,
    refresh_network: bool = False,
    start_proxy: bool = False,
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    config = load_config(config_path)
    checks.append(_task_set_check(config, expected_task_count))
    checks.append(_java_archive_check(java_archive_path))
    checks.append(_plan_check(config, tasks_path))

    proxy_operation = start_relay if start_proxy else relay_status
    try:
        proxy = proxy_operation(source_host=source_proxy_host, source_port=source_proxy_port)
        checks.append(
            _check(
                "proxy-relay",
                proxy.get("status") == "READY",
                "Windows proxy, Podman reverse tunnel and container relay are reachable",
            )
        )
    except (OSError, RuntimeError) as error:
        proxy = {"status": "BLOCKED", "containerProxy": None}
        checks.append(_check("proxy-relay", False, str(error)))

    environment = dict(os.environ)
    environment["HAIFA_EVAL_TASKS_PATH"] = str(tasks_path.resolve())
    environment["HAIFA_EVAL_JAR_PATH"] = str(jar_path.resolve())
    environment["HAIFA_EVAL_JAVA_ARCHIVE_PATH"] = str(java_archive_path.resolve())
    environment["HAIFA_EVAL_EXTRA_DOCKER_COMPOSE"] = str(default_compose_overlay().resolve())
    proxy_url = proxy.get("containerProxy")
    if isinstance(proxy_url, str):
        environment[CONTAINER_PROXY_ENV] = proxy_url
    environment[INFRA_EVIDENCE_ENV] = str(infrastructure_evidence_path.resolve())

    _, provider_target, provider_check = _provider_requirements(config, environment)
    if refresh_network:
        if proxy.get("status") != "READY" or not isinstance(proxy_url, str):
            checks.append(_check("network-preflight-refresh", False, "proxy relay is not ready"))
        elif provider_check.status != "PASS" or provider_target is None:
            checks.append(_check("network-preflight-refresh", False, provider_check.detail))
        else:
            try:
                evidence = run_compose_network_preflight(
                    infrastructure_evidence_path,
                    proxy_url=proxy_url,
                    target_url=provider_target,
                    overlay=default_compose_overlay(),
                    container_cli=container_cli,
                )
                checks.append(
                    _check(
                        "network-preflight-refresh",
                        evidence.get("status") == "READY",
                        "fresh no-model Harbor Compose network evidence generated",
                    )
                )
            except (OSError, RuntimeError, ValueError) as error:
                checks.append(_check("network-preflight-refresh", False, str(error)))

    try:
        doctor_report = doctor(
            config,
            tasks_path,
            admission_path,
            doctor_output_path,
            jar_path=jar_path,
            container_cli=container_cli,
            environment=environment,
            infrastructure_evidence_path=infrastructure_evidence_path,
        )
        checks.extend(
            {
                "name": f"doctor/{item['name']}",
                "status": str(item["status"]),
                "detail": str(item["detail"]),
            }
            for item in doctor_report["checks"]  # type: ignore[union-attr]
        )
    except (OSError, RuntimeError, ValueError) as error:
        checks.append(_check("doctor", False, str(error)))

    free_bytes = shutil.disk_usage(tasks_path).free if tasks_path.exists() else 0
    required_bytes = int(minimum_free_gib * 1024**3)
    checks.append(
        _check(
            "evaluation-disk-headroom",
            free_bytes >= required_bytes,
            f"{free_bytes} bytes free; minimum {required_bytes}",
        )
    )

    status = "READY" if all(item["status"] != "FAIL" for item in checks) else "BLOCKED"
    report: dict[str, Any] = {
        "schemaVersion": 1,
        "status": status,
        "evalId": config.id,
        "model": [candidate.model for candidate in config.candidates],
        "provider": [candidate.resolved_provider() for candidate in config.candidates],
        "expectedTaskCount": expected_task_count,
        "paidModelCalled": False,
        "checks": checks,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail closed unless the frozen SWE-bench batch is ready to evaluate"
    )
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--tasks-path", type=Path, default=default_tasks_path())
    parser.add_argument("--admission", type=Path)
    parser.add_argument("--jar", type=Path, default=_default_haifa_jar())
    parser.add_argument("--java-archive", type=Path, default=default_java_archive_path())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--doctor-output", type=Path)
    parser.add_argument("--infra-evidence", type=Path)
    parser.add_argument("--container-cli", default="podman")
    parser.add_argument("--expected-task-count", type=int, default=30)
    parser.add_argument("--minimum-free-gib", type=float, default=DEFAULT_MINIMUM_FREE_GIB)
    parser.add_argument("--source-proxy-host", default="127.0.0.1")
    parser.add_argument("--source-proxy-port", type=int, default=2081)
    parser.add_argument("--refresh-network", action="store_true")
    parser.add_argument("--start-proxy", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_config(args.config)
    admission = args.admission or (
        repository_root() / "work" / "gates" / "admissions" / f"{config.id}.json"
    )
    output = args.output or (
        repository_root() / "work" / "gates" / "readiness" / f"{config.id}.json"
    )
    doctor_output = args.doctor_output or (
        repository_root() / "work" / "gates" / "doctors" / f"{config.id}-ready.json"
    )
    infrastructure_evidence = args.infra_evidence or (
        repository_root() / "work" / "gates" / "infrastructure" / f"{config.id}-ready-network.json"
    )
    report = verify_readiness(
        config_path=args.config,
        tasks_path=args.tasks_path,
        admission_path=admission,
        jar_path=args.jar,
        java_archive_path=args.java_archive,
        output_path=output,
        doctor_output_path=doctor_output,
        infrastructure_evidence_path=infrastructure_evidence,
        container_cli=args.container_cli,
        expected_task_count=args.expected_task_count,
        minimum_free_gib=args.minimum_free_gib,
        source_proxy_host=args.source_proxy_host,
        source_proxy_port=args.source_proxy_port,
        refresh_network=args.refresh_network,
        start_proxy=args.start_proxy,
    )
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
