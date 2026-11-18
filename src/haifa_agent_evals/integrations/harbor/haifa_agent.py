from __future__ import annotations

import asyncio
import hashlib
import os
import shlex
import tempfile
from pathlib import Path
from typing import override

from harbor.agents.installed.base import BaseInstalledAgent, NonZeroAgentExitCodeError
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from haifa_agent_evals.codex_auth import (
    codex_auth_path,
    minimal_codex_auth,
    minimal_opencode_go_auth,
    opencode_go_auth_path,
)

_CONTAINER_ROOT = "/opt/haifa"
_JAR_PATH = f"{_CONTAINER_ROOT}/haifa-agent.jar"
_CONFIG_PATH = f"{_CONTAINER_ROOT}/haifa-eval.yaml"
_LOOPBACK_RELAY_PATH = f"{_CONTAINER_ROOT}/loopback_tcp_relay.py"
_SQLITE_SNAPSHOT_PATH = f"{_CONTAINER_ROOT}/SqliteSnapshot.java"
_DATABASE_PATH = "/tmp/haifa-runtime.db"
_TRANSCRIPT_ROOT = "/tmp/haifa-transcripts"
_PRIVATE_RUNTIME_ROOT = f"{_CONTAINER_ROOT}/runtime"
_ARCHIVED_DATABASE_PATH = "/logs/agent/haifa-runtime.db"
_ARCHIVED_TRANSCRIPT_ROOT = "/logs/agent/haifa-transcripts"
_JAVA_ARCHIVE_PATH = "/tmp/haifa-java.tar.gz"
_CODEX_AUTH_UPLOAD_PATH = "/tmp/haifa-codex-auth.json"
_CODEX_AUTH_PATH = "/root/.haifa-agent/auth.json"
_OPENCODE_AUTH_HOME = "/opt/haifa/auth-home"
_JAVA_COMMAND = (
    f"JAVA=$([ -x {_CONTAINER_ROOT}/java/bin/java ] && "
    f"echo {_CONTAINER_ROOT}/java/bin/java || command -v java)"
)
# Materialize the task root in OverlayFS before Haifa records its physical identity.
_PREPARE_WORKSPACE = (
    'WORKSPACE=$(pwd -P) && '
    'case "$WORKSPACE" in /app|/app/*|/workspace|/workspace/*) '
    'WORKSPACE_PROBE=$(mktemp -d "$WORKSPACE/.haifa-eval-workspace-XXXXXX") && '
    'rmdir "$WORKSPACE_PROBE";; esac'
)
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


class HaifaAgentAdapter:
    """Thin Harbor adapter around the existing Haifa shaded CLI JAR."""

    def __init__(
        self,
        *args: object,
        jar_path: str | Path | None = None,
        config_path: str | Path | None = None,
        java_archive_path: str | Path | None = None,
        loopback_relay_host: str | None = None,
        loopback_relay_port: int | None = None,
        codex_auth: bool = False,
        opencode_go_auth: bool = False,
        private_runtime_storage: bool = False,
        timeout_seconds: int = 1200,
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
        self.loopback_relay_path = Path(__file__).with_name("loopback_tcp_relay.py")
        self.loopback_relay_digest = _sha256(self.loopback_relay_path)
        self.sqlite_snapshot_path = Path(__file__).with_name("SqliteSnapshot.java")
        self.sqlite_snapshot_digest = _sha256(self.sqlite_snapshot_path)
        self.loopback_relay_host = loopback_relay_host
        self.loopback_relay_port = loopback_relay_port
        self.codex_auth_path = codex_auth_path() if codex_auth else None
        if self.codex_auth_path is not None:
            minimal_codex_auth(self.codex_auth_path)
        self.opencode_go_auth_path = opencode_go_auth_path() if opencode_go_auth else None
        if self.opencode_go_auth_path is not None:
            if codex_auth or loopback_relay_host is not None:
                raise ValueError("OpenCode Go requires its own connection and HTTPS endpoint")
            minimal_opencode_go_auth(self.opencode_go_auth_path)
        if timeout_seconds <= 0:
            raise ValueError("Haifa trial timeout must be positive")
        self.timeout_seconds = timeout_seconds
        self.private_runtime_storage = private_runtime_storage
        self.database_path = (
            f"{_PRIVATE_RUNTIME_ROOT}/haifa-runtime.db"
            if private_runtime_storage
            else _DATABASE_PATH
        )
        self.transcript_root = (
            f"{_PRIVATE_RUNTIME_ROOT}/haifa-transcripts"
            if private_runtime_storage
            else _TRANSCRIPT_ROOT
        )
        if (loopback_relay_host is None) != (loopback_relay_port is None):
            raise ValueError("loopback relay host and port must be configured together")
        if loopback_relay_port is not None and not 1 <= loopback_relay_port <= 65535:
            raise ValueError("loopback relay port must be between 1 and 65535")

    @staticmethod
    @override
    def name() -> str:
        return "haifa"

    @override
    def version(self) -> str | None:
        return f"sha256:{self.jar_digest}"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        directories = f"install -d -m 0755 {_CONTAINER_ROOT}"
        if self.private_runtime_storage:
            directories += f" && install -d -m 0700 {_PRIVATE_RUNTIME_ROOT} {self.transcript_root}"
        else:
            directories += f" && install -d -m 0777 {self.transcript_root}"
        await self.exec_as_root(environment, directories)
        java_probe = await environment.exec(
            command=(
                f"JAVA=$([ -x {_CONTAINER_ROOT}/java/bin/java ] && "
                f"echo {_CONTAINER_ROOT}/java/bin/java || command -v java || true); "
                'test -n "$JAVA" && '
                '"$JAVA" -version 2>&1 | grep -q \'"21\' && '
                "\"$JAVA\" --list-modules 2>/dev/null | grep -q '^jdk.random@'"
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
        await environment.upload_file(self.loopback_relay_path, _LOOPBACK_RELAY_PATH)
        await environment.upload_file(self.sqlite_snapshot_path, _SQLITE_SNAPSHOT_PATH)
        if self.codex_auth_path is not None:
            payload = minimal_codex_auth(self.codex_auth_path)
            with tempfile.TemporaryDirectory(prefix="haifa-eval-codex-auth-") as directory:
                temporary_auth = Path(directory) / "auth.json"
                temporary_auth.write_bytes(payload)
                temporary_auth.chmod(0o600)
                await environment.upload_file(temporary_auth, _CODEX_AUTH_UPLOAD_PATH)
            await self.exec_as_root(
                environment,
                command=(
                    "install -d -m 0700 /root/.haifa-agent && "
                    f"install -m 0600 {_CODEX_AUTH_UPLOAD_PATH} {_CODEX_AUTH_PATH} && "
                    f"rm {_CODEX_AUTH_UPLOAD_PATH}"
                ),
            )
        if self.opencode_go_auth_path is not None:
            payload = minimal_opencode_go_auth(self.opencode_go_auth_path)
            with tempfile.TemporaryDirectory(prefix="haifa-eval-opencode-auth-") as directory:
                temporary_auth = Path(directory) / "auth.json"
                temporary_auth.write_bytes(payload)
                temporary_auth.chmod(0o600)
                await environment.upload_file(temporary_auth, _CODEX_AUTH_UPLOAD_PATH)
            await self.exec_as_root(
                environment,
                command=(
                    f"install -d -m 0700 {_OPENCODE_AUTH_HOME}/.haifa-agent && "
                    f"install -m 0600 {_CODEX_AUTH_UPLOAD_PATH} "
                    f"{_OPENCODE_AUTH_HOME}/.haifa-agent/auth.json && "
                    f"rm {_CODEX_AUTH_UPLOAD_PATH}"
                ),
            )
        await self.exec_as_root(
            environment,
            command=(
                f"chmod 0444 {_JAR_PATH} {_CONFIG_PATH} {_LOOPBACK_RELAY_PATH} "
                f"{_SQLITE_SNAPSHOT_PATH} && "
                f"echo '{self.jar_digest}  {_JAR_PATH}' | sha256sum -c - && "
                f"echo '{self.config_digest}  {_CONFIG_PATH}' | sha256sum -c - && "
                f"echo '{self.loopback_relay_digest}  {_LOOPBACK_RELAY_PATH}' | sha256sum -c - && "
                f"echo '{self.sqlite_snapshot_digest}  {_SQLITE_SNAPSHOT_PATH}' "
                "| sha256sum -c - && "
                f"{_JAVA_COMMAND} && "
                '"$JAVA" -version 2>&1 | grep -q \'"21\' && '
                "\"$JAVA\" --list-modules | grep -q '^jdk.random@' && "
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
        relay_prefix = ""
        if self.loopback_relay_host is not None and self.loopback_relay_port is not None:
            relay_prefix = (
                "PYTHON=$(command -v python3 || find /root/.local/share/uv/python "
                "-path '*/bin/python3.12' -type f | head -n 1); "
                'test -n "$PYTHON"; '
                f'"$PYTHON" {_LOOPBACK_RELAY_PATH} '
                "--listen-host 127.0.0.1 --listen-port 8317 "
                f"--target-host {shlex.quote(self.loopback_relay_host)} "
                f"--target-port {self.loopback_relay_port} & "
                "RELAY_PID=$!; trap 'kill $RELAY_PID 2>/dev/null || true' EXIT; "
                "sleep 1; kill -0 $RELAY_PID; "
            )
        auth_options = ""
        if self.opencode_go_auth_path is not None:
            auth_options = (
                "export HAIFA_CODING_AUTH_STORE=file; "
                f"trap 'rm -f {_OPENCODE_AUTH_HOME}/.haifa-agent/auth.json' EXIT; "
            )
        java_options = (
            f"-Duser.home={_OPENCODE_AUTH_HOME} " if self.opencode_go_auth_path is not None else ""
        )
        command = relay_prefix + auth_options + (
            f"{_JAVA_COMMAND}; "
            f'{_PREPARE_WORKSPACE} && "$JAVA" '
            f'{java_options}-jar {_JAR_PATH} --workspace "$WORKSPACE" --config {_CONFIG_PATH} '
            f"--approval auto --timeout PT{self.timeout_seconds}S --trace jsonl "
            "--trace-file /logs/agent/haifa-trace.jsonl "
            f"--message {shlex.quote(instruction)}"
        )
        try:
            result = await self._execute_cli(environment, command)
        except (asyncio.CancelledError, TimeoutError):
            # Harbor cancels the adapter before it downloads logs or starts the verifier.
            await self._archive_interrupted_run(environment, context)
            raise
        archive = await environment.exec(
            command=(
                "set -eu; "
                f"test -s {self.database_path}; "
                f"find {self.transcript_root} -type f -name '*.jsonl' -size +0c | grep -q .; "
                f"install -m 0600 {self.database_path} {_ARCHIVED_DATABASE_PATH}; "
                f"{self._copy_transcripts_command()}"
            ),
            **({"user": "root"} if self.private_runtime_storage else {}),
        )
        context.metadata = {
            "exit_code": result.return_code,
            "archive_exit_code": archive.return_code,
            "trajectory_archived": archive.return_code == 0,
        }
        if archive.return_code != 0:
            raise RuntimeError(
                "Haifa SQLite/JSONL trajectory could not be archived "
                f"(CLI exit code {result.return_code}, archive exit code {archive.return_code})"
            )
        if result.return_code != 0:
            raise self.nonzero_exit_error(
                f"Haifa CLI exited with code {result.return_code}; verifier remains authoritative"
            )

    def _copy_transcripts_command(self) -> str:
        return (
            f"rm -rf {_ARCHIVED_TRANSCRIPT_ROOT}; "
            f"install -d -m 0700 {_ARCHIVED_TRANSCRIPT_ROOT}; "
            f"cp -a {self.transcript_root}/. {_ARCHIVED_TRANSCRIPT_ROOT}/"
        )

    async def _archive_interrupted_run(
        self, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        metadata: dict[str, object] = {
            "exit_code": None,
            "archive_exit_code": None,
            "database_archived": False,
            "trajectory_archived": False,
        }
        context.metadata = metadata
        user = {"user": "root"} if self.private_runtime_storage else {}
        try:
            async with asyncio.timeout(30):
                snapshot = await environment.exec(
                    command=(
                        f"set -eu; test -s {self.database_path}; umask 077; "
                        'SNAPSHOT=$(mktemp /logs/agent/.haifa-db-XXXXXX); '
                        "trap 'rm -f \"$SNAPSHOT\"' EXIT; "
                        f'{_JAVA_COMMAND}; "$JAVA" -cp {_JAR_PATH} {_SQLITE_SNAPSHOT_PATH} '
                        f'{self.database_path} "$SNAPSHOT" >/dev/null 2>&1; '
                        f'mv -fT -- "$SNAPSHOT" {_ARCHIVED_DATABASE_PATH}'
                    ),
                    **user,
                )
                metadata["archive_exit_code"] = snapshot.return_code
                if snapshot.return_code != 0:
                    return
                metadata["database_archived"] = True
                metadata["archive_exit_code"] = None
                transcripts = await environment.exec(
                    command=(
                        "set -eu; "
                        f"find {self.transcript_root} -type f -name '*.jsonl' -size +0c "
                        f"| grep -q .; {self._copy_transcripts_command()}"
                    ),
                    **user,
                )
                metadata["archive_exit_code"] = transcripts.return_code
                metadata["trajectory_archived"] = transcripts.return_code == 0
        except Exception as exc:
            # Preserve the execution timeout even if the container is already unavailable.
            metadata["archive_error_type"] = type(exc).__name__

    async def _execute_cli(self, environment: BaseEnvironment, command: str):
        return await environment.exec(
            command=command,
            timeout_sec=self.timeout_seconds,
            **({"user": "root"} if self.opencode_go_auth_path is not None else {}),
        )


class HaifaCodingAgent(HaifaAgentAdapter, BaseInstalledAgent):
    nonzero_exit_error = NonZeroAgentExitCodeError
