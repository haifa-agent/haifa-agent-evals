import asyncio
import hashlib

import pytest
from harbor.environments.base import ExecResult
from harbor.environments.docker.docker import DockerEnvironment
from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import TrialPaths

from haifa_agent_evals.integrations.harbor import docker_environment
from haifa_agent_evals.integrations.harbor.docker_environment import EvaluationDockerEnvironment


def test_separate_verifier_starts_with_fresh_container_local_logs(tmp_path, monkeypatch):
    monkeypatch.delenv("HAIFA_EVAL_VERIFIER_UV_ARCHIVE_PATH", raising=False)
    paths = TrialPaths(trial_dir=tmp_path / "trial")
    paths.mkdir()
    paths.reward_text_path.write_text("1\n", encoding="utf-8")
    paths.reward_json_path.write_text('{"reward": 1}\n', encoding="utf-8")
    verifier_mount = {
        "type": "bind",
        "source": paths.verifier_dir.resolve().as_posix(),
        "target": "/logs/verifier",
    }
    resets = []

    async def start(self, force_build):
        assert not self._mounts
        assert list(paths.verifier_dir.iterdir()) == []

    async def reset_dirs(self, *, remove_dirs, create_dirs):
        resets.append((list(map(str, remove_dirs)), list(map(str, create_dirs))))
        return ExecResult(return_code=0)

    monkeypatch.setattr(DockerEnvironment, "start", start)
    monkeypatch.setattr(DockerEnvironment, "reset_dirs", reset_dirs)
    common = {
        "environment_dir": tmp_path,
        "environment_name": "fixture",
        "trial_paths": paths,
        "task_env_config": EnvironmentConfig(docker_image="alpine"),
    }
    agent = EvaluationDockerEnvironment(
        **common,
        session_id="fixture__env",
        mounts=[verifier_mount, {**verifier_mount, "target": "/logs/agent"}],
    )
    verifier = EvaluationDockerEnvironment(
        **common,
        session_id="fixture__verifier__trial",
        mounts=[verifier_mount],
    )

    assert agent.capabilities.mounted
    assert "/logs/verifier" in agent._mount_targets()
    assert not verifier.capabilities.mounted
    asyncio.run(verifier.start(force_build=False))
    assert resets == [(["/logs/verifier"], ["/logs/verifier"])]

    paths.step_verifier_dir("first").mkdir(parents=True)
    step_verifier = EvaluationDockerEnvironment(
        **common,
        session_id="fixture__verifier__first",
        mounts=[verifier_mount],
    )
    assert step_verifier.capabilities.mounted
    assert "/logs/verifier" in step_verifier._mount_targets()


@pytest.mark.parametrize("contents", [None, b"untrusted archive"])
def test_verifier_uv_rejects_missing_or_unpinned_archive(tmp_path, monkeypatch, contents):
    archive = tmp_path / "uv.tar.gz"
    if contents is not None:
        archive.write_bytes(contents)
    monkeypatch.setenv("HAIFA_EVAL_VERIFIER_UV_ARCHIVE_PATH", str(archive))
    with pytest.raises(ValueError, match="verifier uv archive"):
        EvaluationDockerEnvironment(
            environment_dir=tmp_path,
            environment_name="fixture",
            trial_paths=TrialPaths(trial_dir=tmp_path / "trial"),
            task_env_config=EnvironmentConfig(docker_image="debian"),
            session_id="fixture__env",
        )


@pytest.mark.parametrize("install_exit_code", [0, 1])
def test_verifier_uv_is_installed_before_start_returns(tmp_path, monkeypatch, install_exit_code):
    archive = tmp_path / "uv.tar.gz"
    archive.write_bytes(b"fixture archive")
    monkeypatch.setenv("HAIFA_EVAL_VERIFIER_UV_ARCHIVE_PATH", str(archive))
    monkeypatch.setattr(
        docker_environment,
        "VERIFIER_UV_ARCHIVE_SHA256",
        hashlib.sha256(archive.read_bytes()).hexdigest(),
    )
    operations = []

    async def start(self, force_build):
        operations.append("start")

    async def upload_file(self, source, destination):
        assert source == archive
        operations.append("upload")

    async def execute(self, command, *, user):
        assert user == "root"
        operations.append("install")
        return ExecResult(return_code=install_exit_code)

    monkeypatch.setattr(DockerEnvironment, "start", start)
    monkeypatch.setattr(DockerEnvironment, "upload_file", upload_file)
    monkeypatch.setattr(DockerEnvironment, "exec", execute)
    environment = EvaluationDockerEnvironment(
        environment_dir=tmp_path,
        environment_name="fixture",
        trial_paths=TrialPaths(trial_dir=tmp_path / "trial"),
        task_env_config=EnvironmentConfig(docker_image="debian"),
        session_id="fixture__env",
    )
    if install_exit_code:
        with pytest.raises(RuntimeError, match="failed to install the pinned verifier uv"):
            asyncio.run(environment.start(force_build=False))
    else:
        asyncio.run(environment.start(force_build=False))
    assert operations == ["start", "upload", "install"]


@pytest.mark.parametrize("retain_running", [False, True])
def test_requested_running_state_survives_cleanup(tmp_path, monkeypatch, retain_running):
    monkeypatch.delenv("HAIFA_EVAL_VERIFIER_UV_ARCHIVE_PATH", raising=False)
    calls = []

    async def stop(self, delete):
        calls.append(("stop", delete))

    async def prepare_logs(self):
        calls.append(("logs", None))

    monkeypatch.setattr(DockerEnvironment, "stop", stop)
    monkeypatch.setattr(DockerEnvironment, "prepare_logs_for_host", prepare_logs)
    environment = EvaluationDockerEnvironment(
        environment_dir=tmp_path,
        environment_name="fixture",
        trial_paths=TrialPaths(trial_dir=tmp_path / "trial"),
        task_env_config=EnvironmentConfig(docker_image="debian"),
        session_id="fixture__env",
        retain_running_containers=retain_running,
    )
    asyncio.run(environment.stop(delete=True))
    assert calls == ([("logs", None)] if retain_running else [("stop", True)])


@pytest.mark.parametrize("setup_exit_code", [0, 1])
def test_verifier_packages_are_checked_before_agent_setup(tmp_path, monkeypatch, setup_exit_code):
    monkeypatch.delenv("HAIFA_EVAL_VERIFIER_UV_ARCHIVE_PATH", raising=False)
    commands = []

    async def start(self, force_build):
        return None

    async def execute(self, command, *, user, timeout_sec):
        commands.append(command)
        assert user == "root"
        return ExecResult(return_code=setup_exit_code)

    monkeypatch.setattr(DockerEnvironment, "start", start)
    monkeypatch.setattr(DockerEnvironment, "exec", execute)
    environment = EvaluationDockerEnvironment(
        environment_dir=tmp_path,
        environment_name="fixture",
        trial_paths=TrialPaths(trial_dir=tmp_path / "trial"),
        task_env_config=EnvironmentConfig(docker_image="debian"),
        session_id="fixture__env",
        verifier_system_packages=["ca-certificates", "sshpass"],
    )
    if setup_exit_code:
        with pytest.raises(RuntimeError, match="failed to prepare the verifier system packages"):
            asyncio.run(environment.start(force_build=False))
    else:
        asyncio.run(environment.start(force_build=False))
    assert len(commands) == 1


def test_verifier_packages_reject_shell_input(tmp_path, monkeypatch):
    monkeypatch.delenv("HAIFA_EVAL_VERIFIER_UV_ARCHIVE_PATH", raising=False)
    with pytest.raises(ValueError, match="unsupported verifier system package"):
        EvaluationDockerEnvironment(
            environment_dir=tmp_path,
            environment_name="fixture",
            trial_paths=TrialPaths(trial_dir=tmp_path / "trial"),
            task_env_config=EnvironmentConfig(docker_image="debian"),
            session_id="fixture__env",
            verifier_system_packages=["sshpass; false"],
        )


def test_task_scoped_verifier_setup_leaves_other_images_independent(tmp_path, monkeypatch):
    archive = tmp_path / "missing.tar.gz"
    monkeypatch.setenv("HAIFA_EVAL_VERIFIER_UV_ARCHIVE_PATH", str(archive))
    options = {
        "environment_dir": tmp_path,
        "trial_paths": TrialPaths(trial_dir=tmp_path / "trial"),
        "task_env_config": EnvironmentConfig(docker_image="alpine"),
        "session_id": "fixture__env",
        "verifier_uv_tasks": ["needs-uv"],
        "verifier_packages_by_task": {"needs-uv": ["ca-certificates"]},
        "retain_running_tasks": ["needs-uv"],
    }
    environment = EvaluationDockerEnvironment(environment_name="other", **options)
    assert environment._verifier_uv_archive is None
    assert not environment._verifier_system_packages
    assert not environment._retain_running_containers
    with pytest.raises(ValueError, match="verifier uv archive is missing"):
        EvaluationDockerEnvironment(environment_name="needs-uv", **options)
