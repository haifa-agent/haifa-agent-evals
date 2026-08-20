import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.models.agent.context import AgentContext

from haifa_agent_evals.integrations.harbor import haifa_agent as haifa_agent_module
from haifa_agent_evals.integrations.harbor.haifa_agent import HaifaCodingAgent


@dataclass
class _ExecResult:
    return_code: int = 0
    stdout: str = ""
    stderr: str = ""


class _FakeEnvironment:
    def __init__(self, run_exit_code: int = 0) -> None:
        self.default_user = "agent"
        self.run_exit_code = run_exit_code
        self.commands: list[tuple[str, object, dict[str, str] | None]] = []
        self.uploads: list[tuple[Path, str]] = []

    async def exec(
        self,
        command: str,
        user: object = None,
        env: dict[str, str] | None = None,
        **_: object,
    ) -> _ExecResult:
        self.commands.append((command, user, env))
        if "--message" in command:
            return _ExecResult(return_code=self.run_exit_code)
        return _ExecResult()

    async def upload_file(self, source: Path, target: str) -> None:
        self.uploads.append((source, target))


def _agent(tmp_path: Path) -> HaifaCodingAgent:
    jar = tmp_path / "agent.jar"
    config = tmp_path / "config.yaml"
    logs = tmp_path / "logs"
    jar.write_bytes(b"fake jar")
    config.write_text("approval: {mode: auto}\n", encoding="utf-8")
    return HaifaCodingAgent(logs_dir=logs, jar_path=jar, config_path=config)


def test_install_uploads_and_verifies_immutable_inputs(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    environment = _FakeEnvironment()

    asyncio.run(agent.install(environment))  # type: ignore[arg-type]

    assert [target for _, target in environment.uploads] == [
        "/opt/haifa/haifa-agent.jar",
        "/opt/haifa/haifa-eval.yaml",
    ]
    all_commands = "\n".join(command for command, _, _ in environment.commands)
    assert agent.jar_digest in all_commands
    assert agent.config_digest in all_commands
    assert "--help" in all_commands
    assert "sha256sum -c" in all_commands
    assert "install -d -m 0777 /tmp/haifa-transcripts" in all_commands


def test_install_can_upload_one_pinned_java_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    java_archive = tmp_path / "jdk.tar.gz"
    java_archive.write_bytes(b"pinned jdk")
    monkeypatch.setattr(
        haifa_agent_module,
        "_JAVA_ARCHIVE_SHA256",
        haifa_agent_module._sha256(java_archive),
    )
    jar = tmp_path / "agent.jar"
    config = tmp_path / "config.yaml"
    jar.write_bytes(b"fake jar")
    config.write_text("approval: {mode: auto}\n", encoding="utf-8")
    agent = HaifaCodingAgent(
        logs_dir=tmp_path / "logs",
        jar_path=jar,
        config_path=config,
        java_archive_path=java_archive,
    )
    environment = _FakeEnvironment()

    asyncio.run(agent.install(environment))  # type: ignore[arg-type]

    assert environment.uploads[0] == (java_archive, "/tmp/haifa-java.tar.gz")
    install_command = environment.commands[1][0]
    assert "curl -L" not in install_command
    assert "jdk.random" in install_command


@pytest.mark.parametrize("exit_code", [0, 1, 2])
def test_run_preserves_exit_code_and_quotes_instruction(tmp_path: Path, exit_code: int) -> None:
    agent = _agent(tmp_path)
    environment = _FakeEnvironment(run_exit_code=exit_code)
    context = AgentContext()
    instruction = "fix 'quoted'\ntext; echo unsafe"

    if exit_code:
        with pytest.raises(NonZeroAgentExitCodeError):
            asyncio.run(agent.run(instruction, environment, context))  # type: ignore[arg-type]
    else:
        asyncio.run(agent.run(instruction, environment, context))  # type: ignore[arg-type]

    command = environment.commands[-1][0]
    assert "'fix '" in command
    assert "echo unsafe'" in command
    assert context.metadata == {"exit_code": exit_code}
    assert "DEEPSEEK_API_KEY" not in command
