"""The dataset artifact parses into the typed models and keeps its invariants."""

from collections import Counter

import pytest

from cordon.dataset import DEFAULT_DATASET_PATH, dataset_hash, load_dataset
from cordon.schema import Category, Decision

CORRECTABLE_DRIFT_ATTRIBUTES = {"encryption", "versioning", "public_access"}


@pytest.fixture(scope="session")
def dataset():
    return load_dataset()


def test_shape(dataset):
    assert len(dataset.scenarios) == 240
    counts = Counter(s.category for s in dataset.scenarios)
    assert all(n == 30 for n in counts.values())
    assert len(counts) == 8


def test_action_catalog(dataset):
    names = dataset.action_names()
    assert len(names) == 10
    assert "set_bucket_public_access" in names
    assert "inspect_resource_state" in names
    assert dataset.mutating_action_names() == names - {"inspect_resource_state"}


def test_policy_refs_resolve(dataset):
    policy_ids = {p.policy_id for p in dataset.policies}
    for s in dataset.scenarios:
        assert set(s.policy_refs) <= policy_ids, s.scenario_id


def test_drift_scenarios_are_correctable(dataset):
    """Every drift attribute must have a corrective action in the catalog."""
    drifted = [s for s in dataset.scenarios if s.drift_spec is not None]
    assert len(drifted) == 20
    for s in drifted:
        assert s.drift_spec.attribute in CORRECTABLE_DRIFT_ATTRIBUTES, s.scenario_id
        assert s.expected_decision is Decision.RECONCILE


def test_drift_controls(dataset):
    controls = [
        s
        for s in dataset.scenarios
        if s.category is Category.CONFIGURATION_DRIFT and s.drift_spec is None
    ]
    assert len(controls) == 10
    assert all(s.expected_decision is Decision.ALLOW for s in controls)


def test_oracle_present(dataset):
    for s in dataset.scenarios:
        assert len(s.assertions) >= 1, s.scenario_id


def test_hash_is_stable(dataset):
    assert dataset_hash() == dataset_hash(DEFAULT_DATASET_PATH)
    assert len(dataset_hash()) == 64
