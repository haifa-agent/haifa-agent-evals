from __future__ import annotations

import shutil
from contextlib import suppress

from harbor.environments.capabilities import EnvironmentCapabilities
from harbor.environments.docker.docker import DockerEnvironment
from harbor.models.task.config import EnvironmentConfig, TaskOS
from harbor.models.trial.config import ServiceVolumeConfig
from harbor.models.trial.paths import EnvironmentPaths, TrialPaths


class EvaluationDockerEnvironment(DockerEnvironment):
    """Keep single-step Linux verifier logs in Docker until Harbor copies them out."""

    def __init__(
        self,
        *,
        trial_paths: TrialPaths,
        task_env_config: EnvironmentConfig,
        mounts: list[ServiceVolumeConfig] | None = None,
        **kwargs,
    ) -> None:
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
        if self._local_verifier_logs:
            verifier_dir = EnvironmentPaths.for_os(self.os).verifier_dir
            result = await self.reset_dirs(remove_dirs=[verifier_dir], create_dirs=[verifier_dir])
            if result.return_code:
                raise RuntimeError("failed to initialize the container-local verifier logs")
