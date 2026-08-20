from __future__ import annotations

import hashlib
import os
import shlex
from pathlib import Path
from typing import override

from harbor.agents.installed.base import BaseInstalledAgent, NonZeroAgentExitCodeError
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

_CONTAINER_ROOT = "/opt/haifa"
_JAR_PATH = f"{_CONTAINER_ROOT}/haifa-agent.jar"
_CONFIG_PATH = f"{_CONTAINER_ROOT}/haifa-eval.yaml"
_TRANSCRIPT_ROOT = "/tmp/haifa-transcripts"
_JAVA_ARCHIVE_PATH = "/tmp/haifa-java.tar.gz"
_JAVA_ARCHIVE_URL = (
    "https://github.com/adoptium/temurin21-binaries/releases/download/"
    "jdk-21.0.8%2B9/OpenJDK21U-jdk_x64_linux_hotspot_21.0.8_9.tar.gz"
)
_JAVA_ARCHIVE_SHA256 = "f2dc5418092c43003db8f9005c4a286e1c0104fea96ccdd49e8ebd037cac9219"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class HaifaCodingAgent(BaseInstalledAgent):
    """Thin Harbor adapter around the existing Haifa shaded CLI JAR."""

    def __init__(
        self,
        *args: object,
        jar_path: str | Path | None = None,
        config_path: str | Path | None = None,
        java_archive_path: str | Path | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        configured_jar = jar_path or os.environ.get("HAIFA_EVAL_JAR_PATH")
        if not configured_jar:
            raise ValueError("HAIFA_EVAL_JAR_PATH is required")
        self.jar_path = Path(configured_jar).expanduser().resolve()
        self.config_path = (
            Path(config_path).expanduser().resolve()
            if config_path
            else Path(__file__).with_name("haifa-eval.yaml")
        )
        configured_java = java_archive_path or os.environ.get("HAIFA_EVAL_JAVA_ARCHIVE_PATH")
        self.java_archive_path = (
            Path(configured_java).expanduser().resolve() if configured_java else None
        )
        if not self.jar_path.is_file():
            raise ValueError("Haifa CLI JAR does not exist")
        if not self.config_path.is_file():
            raise ValueError("Haifa eval configuration does not exist")
        if self.java_archive_path is not None:
            if not self.java_archive_path.is_file():
                raise ValueError("Haifa eval Java archive does not exist")
            if _sha256(self.java_archive_path) != _JAVA_ARCHIVE_SHA256:
                raise ValueError("Haifa eval Java archive digest does not match the pinned JDK")
        self.jar_digest = _sha256(self.jar_path)
        self.config_digest = _sha256(self.config_path)

    @staticmethod
    @override
    def name() -> str:
        return "haifa"

    @override
    def version(self) -> str | None:
        return f"sha256:{self.jar_digest}"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await self.exec_as_root(
            environment,
            f"install -d -m 0755 {_CONTAINER_ROOT} && "
            f"install -d -m 0777 {_TRANSCRIPT_ROOT}",
        )
        java_probe = await environment.exec(
            command=(
                f"JAVA=$([ -x {_CONTAINER_ROOT}/java/bin/java ] && "
                f"echo {_CONTAINER_ROOT}/java/bin/java || command -v java || true); "
                'test -n "$JAVA" && '
                '"$JAVA" -version 2>&1 | grep -q \'"21\' && '
                '"$JAVA" --list-modules 2>/dev/null | grep -q \'^jdk.random@\''
            ),
            user="root",
        )
        if java_probe.return_code != 0:
            if self.java_archive_path is not None:
                await environment.upload_file(self.java_archive_path, _JAVA_ARCHIVE_PATH)
                obtain_java = "true"
            else:
                obtain_java = (
                    "apt-get update && apt-get install -y curl ca-certificates && "
                    f"curl -L --fail --retry 3 --retry-all-errors "
                    f"-o {_JAVA_ARCHIVE_PATH} {_JAVA_ARCHIVE_URL}"
                )
            await self.exec_as_root(
                environment,
                command=(
                    f"{obtain_java} && "
                    f"echo '{_JAVA_ARCHIVE_SHA256}  {_JAVA_ARCHIVE_PATH}' | sha256sum -c - && "
                    f"mkdir -p {_CONTAINER_ROOT}/java && "
                    f"tar -xzf {_JAVA_ARCHIVE_PATH} -C {_CONTAINER_ROOT}/java "
                    "--strip-components=1 && "
                    f"rm {_JAVA_ARCHIVE_PATH}"
                ),
                env={"DEBIAN_FRONTEND": "noninteractive"},
            )
        await environment.upload_file(self.jar_path, _JAR_PATH)
        await environment.upload_file(self.config_path, _CONFIG_PATH)
        await self.exec_as_root(
            environment,
            command=(
                f"chmod 0444 {_JAR_PATH} {_CONFIG_PATH} && "
                f"echo '{self.jar_digest}  {_JAR_PATH}' | sha256sum -c - && "
                f"echo '{self.config_digest}  {_CONFIG_PATH}' | sha256sum -c - && "
                f"JAVA=$([ -x {_CONTAINER_ROOT}/java/bin/java ] && "
                f"echo {_CONTAINER_ROOT}/java/bin/java || command -v java) && "
                '"$JAVA" -version 2>&1 | grep -q \'"21\' && '
                '"$JAVA" --list-modules | grep -q \'^jdk.random@\' && '
                f'"$JAVA" -jar {_JAR_PATH} --help >/dev/null'
            ),
        )

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        command = (
            f"JAVA=$([ -x {_CONTAINER_ROOT}/java/bin/java ] && "
            f"echo {_CONTAINER_ROOT}/java/bin/java || command -v java); "
            'WORKSPACE=$(pwd -P); "$JAVA" '
            f'-jar {_JAR_PATH} --workspace "$WORKSPACE" --config {_CONFIG_PATH} '
            "--approval auto --timeout PT20M --trace jsonl "
            "--trace-file /logs/agent/haifa-trace.jsonl "
            f"--message {shlex.quote(instruction)}"
        )
        result = await environment.exec(command=command, timeout_sec=1200)
        context.metadata = {"exit_code": result.return_code}
        if result.return_code != 0:
            raise NonZeroAgentExitCodeError(
                f"Haifa CLI exited with code {result.return_code}; verifier remains authoritative"
            )
