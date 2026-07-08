"""Plan cache round-trip: pre-generate with a fake client, replay from disk."""

import json
from types import SimpleNamespace

import pytest

from cordon.dataset import load_dataset
from cordon.planner import PARAM_FIELDS, OpenAIPlanner
from cordon.plancache import (
    cache_path, cache_summary, load_cached_planner, pregenerate_plans,
)


@pytest.fixture(scope="session")
def dataset():
    return load_dataset()


class ScriptedClient:
    """Returns a fixed plan for every call; records how many calls happened."""

    def __init__(self, action_name="inspect_resource_state"):
        self.action_name = action_name
        self.calls = 0
        self.responses = self

    def create(self, **kwargs):
        self.calls += 1
        action = {"action_name": self.action_name} | {k: None for k in PARAM_FIELDS}
        if self.action_name == "inspect_resource_state":
            action |= {"resource_type": "storage_bucket", "resource_id": "x"}
        payload = json.dumps({"intent_summary": "s", "actions": [action]})
        return SimpleNamespace(output_text=payload, id="resp", model=kwargs["model"],
                               usage=SimpleNamespace(input_tokens=100, output_tokens=20))


def make_planner(dataset, client, variant="core"):
    return OpenAIPlanner(dataset.actions, dataset.policies, model="test-model",
                         variant=variant, client=client)


def test_pregenerate_then_replay(dataset, tmp_path):
    client = ScriptedClient()
    planner = make_planner(dataset, client)
    subset = dataset.scenarios[:3]

    pregenerate_plans(dataset, planner, reps=2, out_root=tmp_path,
                      concurrency=4, scenarios=subset, progress_every=1000)
    assert client.calls == 6  # 3 scenarios x 2 reps

    path = cache_path(tmp_path, "test-model", "core")
    assert path.exists()

    cached = load_cached_planner(tmp_path, "test-model", "core")
    for s in subset:
        for rep in (0, 1):
            plan, info = cached.plan_for(s, rep)
            assert plan.actions[0].action_name == "inspect_resource_state"
            assert info.tokens_in == 100


def test_pregenerate_resumes(dataset, tmp_path):
    client = ScriptedClient()
    planner = make_planner(dataset, client)
    subset = dataset.scenarios[:2]

    pregenerate_plans(dataset, planner, reps=1, out_root=tmp_path, scenarios=subset)
    assert client.calls == 2
    # second pass fills nothing new
    pregenerate_plans(dataset, planner, reps=1, out_root=tmp_path, scenarios=subset)
    assert client.calls == 2

    summary = cache_summary(tmp_path, "test-model", "core")
    assert summary["total"] == 2 and summary["errors"] == 0


def test_missing_key_is_error(dataset, tmp_path):
    client = ScriptedClient()
    planner = make_planner(dataset, client)
    pregenerate_plans(dataset, planner, reps=1, out_root=tmp_path,
                      scenarios=dataset.scenarios[:1])
    cached = load_cached_planner(tmp_path, "test-model", "core")
    from cordon.planner import PlannerError
    with pytest.raises(PlannerError):
        cached.plan_for(dataset.scenarios[5], 0)
