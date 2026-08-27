import json
from pathlib import Path
from subprocess import CompletedProcess

import yaml
from harbor.publisher.packager import Packager

from haifa_agent_evals import environment_baseline


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    task_id = "swe-bench/psf__requests-1142"
    tasks = tmp_path / "source"
    task = tasks / "psf__requests-1142"
    (task / "environment").mkdir(parents=True)
    (task / "tests").mkdir()
    (task / "solution").mkdir()
    (task / "task.toml").write_text(
        'version = "1.0"\n[task]\nname = "swe-bench/psf__requests-1142"\n[environment]\ncpus = 1\n',
        encoding="utf-8",
    )
    (task / "instruction.md").write_text("fix it\n", encoding="utf-8")
    (task / "environment" / "Dockerfile").write_text("FROM source\n", encoding="utf-8")
    (task / "tests" / "test.sh").write_text("true\n", encoding="utf-8")
    (task / "solution" / "solve.sh").write_text("true\n", encoding="utf-8")
    task_digest = f"sha256:{Packager.compute_content_hash(task)[0]}"
    config = tmp_path / "eval.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "id": "swebench-smoke",
                "dataset": f"swe-bench/swe-bench-verified@sha256:{'a' * 64}",
                "datasetTrust": "upstream-verified",
                "tasks": [task_id],
                "attempts": 1,
                "timeoutMinutes": 20,
                "candidates": [
                    {"id": "haifa", "agent": "package:Haifa", "model": "deepseek/model"}
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    admission = tmp_path / "admission.json"
    admission.write_text(
        json.dumps(
            {
                "evalId": "swebench-smoke",
                "dataset": f"swe-bench/swe-bench-verified@sha256:{'a' * 64}",
                "status": "ADMITTED",
                "trustMode": "UPSTREAM_VERIFIED",
                "tasks": [
                    {
                        "task_id": task_id,
                        "task_digest": task_digest,
                        "status": "ADMITTED",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return config, tasks, admission, task_digest


def _inspected(working_dir: str = "/testbed") -> dict[str, object]:
    return {
        "Id": "sha256:image-id",
        "Digest": "sha256:image-digest",
        "RepoDigests": [
            "localhost/haifa-agent-evals/swebench-psf__requests-1142@sha256:image-digest"
        ],
        "Size": 123,
        "Architecture": "amd64",
        "Os": "linux",
        "Config": {"WorkingDir": working_dir},
    }


def test_freeze_and_validate_swebench_environment(tmp_path: Path, monkeypatch) -> None:
    config, tasks, admission, source_digest = _fixture(tmp_path)
    monkeypatch.setattr(environment_baseline, "_container_cli", lambda _value: "podman")
    monkeypatch.setattr(environment_baseline, "_image_inventory", lambda _cli: [])

    def inspect(_cli: str, image: str) -> dict[str, object]:
        inspected = _inspected()
        if "@sha256:" not in image:
            inspected["Digest"] = "sha256:tag-view-digest"
        return inspected

    monkeypatch.setattr(environment_baseline, "_inspect", inspect)
    monkeypatch.setattr(
        environment_baseline.subprocess,
        "run",
        lambda command, **_kwargs: CompletedProcess(command, 0),
    )
    output = tmp_path / "baseline"

    result = environment_baseline.freeze_swebench_task_environments(
        config,
        tasks,
        admission,
        output,
        container_cli="podman",
        source_images=["cached-task-image"],
    )

    lock = json.loads((output / environment_baseline.LOCK_NAME).read_text(encoding="utf-8"))
    frozen_task = output / "tasks" / "psf__requests-1142"
    assert result["reused"] is False
    assert lock["sourceTaskDigests"] == {"swe-bench/psf__requests-1142": source_digest}
    assert lock["frozenTaskDigests"]["swe-bench/psf__requests-1142"].startswith("sha256:")
    assert lock["images"]["swe-bench/psf__requests-1142"]["digest"] == "sha256:image-digest"
    assert "docker_image" in (frozen_task / "task.toml").read_text(encoding="utf-8")
    assert (
        environment_baseline.validate_environment_baseline(
            environment_baseline.load_config(config),
            output / "tasks",
            admission,
            container_cli="podman",
        )
        == lock
    )


def test_freeze_accepts_trailing_slash_in_testbed_working_dir(
    tmp_path: Path, monkeypatch
) -> None:
    config, tasks, admission, _ = _fixture(tmp_path)
    inventory = [
        {
            **_inspected("/testbed/"),
            "RepoTags": ["docker.io/swebench/sweb.eval.x86_64.psf__requests-1142:latest"],
        }
    ]
    monkeypatch.setattr(environment_baseline, "_container_cli", lambda _value: "podman")
    monkeypatch.setattr(environment_baseline, "_image_inventory", lambda _cli: inventory)
    monkeypatch.setattr(
        environment_baseline, "_inspect", lambda _cli, _image: _inspected("/testbed/")
    )
    monkeypatch.setattr(
        environment_baseline.subprocess,
        "run",
        lambda command, **_kwargs: CompletedProcess(command, 0),
    )

    result = environment_baseline.freeze_swebench_task_environments(
        config,
        tasks,
        admission,
        tmp_path / "baseline",
        container_cli="podman",
    )

    assert result["reused"] is False


def test_freeze_rejects_source_task_not_matching_admission(tmp_path: Path, monkeypatch) -> None:
    config, tasks, admission, _ = _fixture(tmp_path)
    (tasks / "psf__requests-1142" / "instruction.md").write_text("changed\n", encoding="utf-8")
    monkeypatch.setattr(environment_baseline, "_container_cli", lambda _value: "podman")
    monkeypatch.setattr(environment_baseline, "_image_inventory", lambda _cli: [])

    try:
        environment_baseline.freeze_swebench_task_environments(
            config,
            tasks,
            admission,
            tmp_path / "baseline",
            source_images=["cached-task-image"],
        )
    except ValueError as error:
        assert "source task digest does not match admission" in str(error)
    else:
        raise AssertionError("expected source digest validation failure")
