import inspect

import pytest


def test_pier_can_instantiate_the_haifa_adapter() -> None:
    module = pytest.importorskip("haifa_agent_evals.integrations.pier.haifa_agent")
    assert not inspect.isabstract(module.HaifaCodingAgent)
