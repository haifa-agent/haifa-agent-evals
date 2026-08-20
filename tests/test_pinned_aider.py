import asyncio
from dataclasses import dataclass
from pathlib import Path

from haifa_agent_evals.integrations.harbor.pinned_aider import PinnedAiderAgent


@dataclass
class _ExecResult:
    return_code: int = 0
    stdout: str = ""
    stderr: str = ""


class _FakeEnvironment:
    def __init__(self, installed: bool) -> None:
        self.default_user = "root"
        self.installed = installed
        self.commands: list[str] = []

    async def exec(self, command: str, **_: object) -> _ExecResult:
        self.commands.append(command)
        if len(self.commands) == 1:
            return _ExecResult(return_code=0 if self.installed else 1)
        return _ExecResult()


def _agent(tmp_path: Path) -> PinnedAiderAgent:
    return PinnedAiderAgent(logs_dir=tmp_path / "logs", model_name="openai/model")


def test_reuses_exact_preinstalled_aider(tmp_path: Path) -> None:
    environment = _FakeEnvironment(installed=True)

    asyncio.run(_agent(tmp_path).install(environment))  # type: ignore[arg-type]

    assert len(environment.commands) == 1
    assert "aider 0.86.2" in environment.commands[0]


def test_fallback_install_is_pinned(tmp_path: Path) -> None:
    environment = _FakeEnvironment(installed=False)

    asyncio.run(_agent(tmp_path).install(environment))  # type: ignore[arg-type]

    commands = "\n".join(environment.commands)
    assert "aider-chat==0.86.2" in commands
    assert "uv/0.5.9/install.sh" in commands
    assert "--python 3.12.8" in commands
