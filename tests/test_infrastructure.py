import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from haifa_agent_evals import infrastructure, proxy_relay


def test_harbor_preflight_job_uses_oracle_and_real_compose_overlay(tmp_path: Path) -> None:
    overlay = tmp_path / "proxy.compose.yaml"
    overlay.write_text("services: {}\n", encoding="utf-8")

    config = infrastructure._job_config(
        "infra-check",
        tmp_path,
        tmp_path / "tasks",
        overlay,
    )

    assert config["agents"] == [
        {
            "name": "oracle",
            "model_name": "infrastructure-preflight",
            "override_timeout_sec": 60,
            "max_timeout_sec": 60,
            "override_setup_timeout_sec": 60,
        }
    ]
    assert config["environment"]["extra_docker_compose"] == [str(overlay.resolve())]
    assert config["datasets"][0]["task_names"] == ["harbor-compose-network"]


def test_infrastructure_evidence_must_match_overlay_proxy_backend_and_expiry(
    tmp_path: Path,
) -> None:
    overlay = tmp_path / "proxy.compose.yaml"
    overlay.write_text("services: {}\n", encoding="utf-8")
    now = datetime(2026, 8, 22, 3, 0, tzinfo=UTC)
    evidence = tmp_path / "infra.json"
    evidence.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "READY",
                "checkedAt": now.isoformat(),
                "expiresAt": (now + timedelta(minutes=30)).isoformat(),
                "containerBackend": "podman",
                "networkProbe": {
                    "composeNetworkVerified": True,
                    "targetHost": "api.deepseek.com",
                    "proxyEndpoint": {
                        "scheme": "http",
                        "host": "host.containers.internal",
                        "port": 22081,
                    },
                    "overlaySha256": infrastructure._sha256(overlay),
                },
            }
        ),
        encoding="utf-8",
    )

    ok, detail, digest = infrastructure.validate_infrastructure_evidence(
        evidence,
        overlay=overlay,
        proxy_url="http://host.containers.internal:22081",
        container_cli="C:/tools/podman.exe",
        target_url="https://api.deepseek.com/",
        now=now,
    )

    assert ok is True
    assert detail == "fresh Harbor Compose network preflight matched"
    assert digest == infrastructure._sha256(evidence)

    expired, detail, _ = infrastructure.validate_infrastructure_evidence(
        evidence,
        overlay=overlay,
        proxy_url="http://host.containers.internal:22081",
        container_cli="podman",
        target_url="https://api.deepseek.com/",
        now=now + timedelta(minutes=31),
    )
    assert expired is False
    assert detail == "Harbor Compose network preflight evidence has expired"

    mismatch, detail, _ = infrastructure.validate_infrastructure_evidence(
        evidence,
        overlay=overlay,
        proxy_url="http://host.containers.internal:22081",
        container_cli="podman",
        target_url="https://workspace-123.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        now=now,
    )
    assert mismatch is False
    assert detail == "preflight target does not match the evaluation provider"


def test_proxy_status_recognizes_healthy_external_relay(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(proxy_relay, "_port_open", lambda host, port: True)
    monkeypatch.setattr(
        proxy_relay,
        "_machine",
        lambda: {"Name": "podman-machine-default", "State": "running"},
    )
    monkeypatch.setattr(proxy_relay, "_vm_port_open", lambda port: True)

    status = proxy_relay.relay_status(state_path=tmp_path / "missing.json")

    assert status["status"] == "READY"
    assert status["managed"] is False
    assert status["containerProxy"] == "http://host.containers.internal:22081"
