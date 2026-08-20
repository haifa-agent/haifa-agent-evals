from __future__ import annotations

from typing import override

from harbor.agents.installed.aider import Aider
from harbor.environments.base import BaseEnvironment

_AIDER_VERSION = "0.86.2"
_UV_VERSION = "0.5.9"
_PYTHON_VERSION = "3.12.8"


class PinnedAiderAgent(Aider):
    """Aider adapter that reuses the exact preinstalled evaluation version."""

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        probe = await environment.exec(
            command=(
                'if [ -f "$HOME/.local/bin/env" ]; then . "$HOME/.local/bin/env"; fi; '
                f'test "$(aider --version 2>/dev/null)" = "aider {_AIDER_VERSION}"'
            ),
            user=environment.default_user,
        )
        if probe.return_code == 0:
            return

        await self.exec_as_root(
            environment,
            command="apt-get update && apt-get install -y curl ca-certificates",
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                f'curl --fail --location --retry 5 --retry-all-errors '
                f'"https://astral.sh/uv/{_UV_VERSION}/install.sh" | sh; '
                'if [ -f "$HOME/.local/bin/env" ]; then . "$HOME/.local/bin/env"; fi; '
                f'uv tool install --force --python {_PYTHON_VERSION} '
                f'"aider-chat=={_AIDER_VERSION}"; '
                f'test "$(aider --version)" = "aider {_AIDER_VERSION}"'
            ),
        )

