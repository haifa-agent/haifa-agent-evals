from __future__ import annotations

import hashlib
import os
import shutil
from contextlib import suppress
from pathlib import Path

from harbor.environments.capabilities import EnvironmentCapabilities
from harbor.environments.docker.docker import DockerEnvironment
from harbor.models.task.config import EnvironmentConfig, TaskOS
from harbor.models.trial.config import ServiceVolumeConfig
from harbor.models.trial.paths import EnvironmentPaths, TrialPaths

VERIFIER_UV_ARCHIVE_SHA256 = "2cf10babba653310606f8b49876cfb679928669e7ddaa1fb41fb00ce73e64f66"
_VERIFIER_PACKAGE_PROBES = {
    "ca-certificates": "test -s /etc/ssl/certs/ca-certificates.crt",
    "sshpass": "command -v sshpass >/dev/null 2>&1",
}
_VERIFIER_UV_ARCHIVE = "/tmp/haifa-verifier-uv.tar.gz"
_VERIFIER_UV_INSTALL = f"""set -eu
test "$(uname -m)" = x86_64
printf '%s  %s\\n' '{VERIFIER_UV_ARCHIVE_SHA256}' '{_VERIFIER_UV_ARCHIVE}' | sha256sum -c -
mkdir -p /opt/haifa/verifier-uv /usr/local/bin "$HOME/.local/bin"
tar -xzf {_VERIFIER_UV_ARCHIVE} -C /opt/haifa/verifier-uv --strip-components=1
install -m 0755 /opt/haifa/verifier-uv/uv /usr/local/bin/uv
install -m 0755 /opt/haifa/verifier-uv/uvx /usr/local/bin/uvx
printf 'export PATH="/usr/local/bin:$PATH"\\n' > "$HOME/.local/bin/env"
/usr/local/bin/uv --version | grep -Eq '^uv 0[.]9[.]5([[:space:]]|$)'
/usr/local/bin/uvx --version | grep -Eq '^uvx 0[.]9[.]5([[:space:]]|$)'
rm {_VERIFIER_UV_ARCHIVE}
"""


class EvaluationDockerEnvironment(DockerEnvironment):
    """Keep single-step Linux verifier logs in Docker until Harbor copies them out."""

    def __init__(
        self,
        *,
        trial_paths: TrialPaths,
        task_env_config: EnvironmentConfig,
        mounts: list[ServiceVolumeConfig] | None = None,
        verifier_system_packages: list[str] | None = None,
        retain_running_containers: bool = False,
        **kwargs,
    ) -> None:
        self._retain_running_containers = retain_running_containers
        self._verifier_system_packages = tuple(verifier_system_packages or ())
        if any(name not in _VERIFIER_PACKAGE_PROBES for name in self._verifier_system_packages):
            raise ValueError("unsupported verifier system package")
        if self._verifier_system_packages and task_env_config.os != TaskOS.LINUX:
            raise ValueError("verifier system packages require a Linux environment")
        configured_uv = os.environ.get("HAIFA_EVAL_VERIFIER_UV_ARCHIVE_PATH")
        self._verifier_uv_archive = (
            Path(configured_uv).expanduser().resolve() if configured_uv else None
        )
        if self._verifier_uv_archive is not None:
            if task_env_config.os != TaskOS.LINUX:
                raise ValueError("the pinned verifier uv archive requires a Linux environment")
            if not self._verifier_uv_archive.is_file():
                raise ValueError("the pinned verifier uv archive is missing")
            with self._verifier_uv_archive.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != VERIFIER_UV_ARCHIVE_SHA256:
                    raise ValueError("the verifier uv archive does not match uv 0.9.5 Linux x86_64")
        verifier_dir = EnvironmentPaths.for_os(task_env_config.os).verifier_dir
        # Harbor 0.20 supplies just this bind for a separate verifier. A
        # multi-step trial creates its step directories first and may still have
        # a running agent that uses the original bind in a subsequent step.
        self._local_verifier_logs = (
            task_env_config.os == TaskOS.LINUX
            and not trial_paths.steps_dir.exists()
            and mounts is not None
            and len(mounts) == 1
            and mounts[0].get("type") == "bind"
            and mounts[0].get("source") == trial_paths.verifier_dir.resolve().as_posix()
            and mounts[0].get("target") == str(verifier_dir)
        )
        super().__init__(
            trial_paths=trial_paths,
            task_env_config=task_env_config,
            mounts=[] if self._local_verifier_logs else mounts,
            **kwargs,
        )

    @property
    def capabilities(self) -> EnvironmentCapabilities:
        capabilities = super().capabilities
        if self._local_verifier_logs:
            return capabilities.model_copy(update={"mounted": False})
        return capabilities

    async def start(self, force_build: bool) -> None:
        if self._local_verifier_logs:
            # The earlier agent environment mounted this host directory. Discard any
            # stale reward before Harbor copies and parses the verifier's own logs.
            host_logs = self.trial_paths.verifier_dir
            with suppress(FileNotFoundError):
                shutil.rmtree(host_logs)
            host_logs.mkdir(mode=0o700)
        await super().start(force_build=force_build)
        if self._verifier_system_packages:
            checks = [
                f'if ! {_VERIFIER_PACKAGE_PROBES[name]}; then PACKAGES="$PACKAGES {name}"; fi'
                for name in self._verifier_system_packages
            ]
            command = "\n".join([
                "set -eu",
                "PACKAGES=''",
                *checks,
                'if [ -n "$PACKAGES" ]; then',
                "apt-get update",
                "DEBIAN_FRONTEND=noninteractive apt-get install -y "
                "--no-install-recommends $PACKAGES",
                "fi",
                *(_VERIFIER_PACKAGE_PROBES[name] for name in self._verifier_system_packages),
            ])
            result = await self.exec(command, user="root", timeout_sec=600)
            if result.return_code:
                raise RuntimeError("failed to prepare the verifier system packages")
        if self._verifier_uv_archive is not None:
            await self.upload_file(self._verifier_uv_archive, _VERIFIER_UV_ARCHIVE)
            result = await self.exec(_VERIFIER_UV_INSTALL, user="root")
            if result.return_code:
                raise RuntimeError("failed to install the pinned verifier uv and uvx")
        if self._local_verifier_logs:
            verifier_dir = EnvironmentPaths.for_os(self.os).verifier_dir
            result = await self.reset_dirs(remove_dirs=[verifier_dir], create_dirs=[verifier_dir])
            if result.return_code:
                raise RuntimeError("failed to initialize the container-local verifier logs")

    async def stop(self, delete: bool) -> None:
        if self._retain_running_containers:
            await self.prepare_logs_for_host()
            return
        await super().stop(delete=delete)
