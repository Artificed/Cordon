import pytest

from cordon.schema import PlannedAction, Resource
from cordon.state_model import StateModel


def act(name, **params):
    return PlannedAction(action_name=name, parameters=params)


def bucket(rid="staging-assets-bucket", env="staging", desired=None, **attrs):
    attributes = {"encryption": "enabled", "public_access": False} | attrs
    if desired is not None:
        attributes["desired_state"] = desired
    return Resource(resource_type="storage_bucket", resource_id=rid,
                    environment=env, attributes=attributes)


@pytest.fixture
def model():
    return StateModel.from_initial_state([bucket(desired={"encryption": "enabled"})])


def test_desired_state_separated_from_beliefs(model):
    record = model.get("staging-assets-bucket")
    assert record.desired_state == {"encryption": "enabled"}
    assert "desired_state" not in record.attributes


def test_create_precondition(model):
    err = model.check_action(act("create_storage_bucket",
                                 bucket_name="staging-assets-bucket", environment="staging"))
    assert err.startswith("state_mismatch:already_exists")
    assert model.check_action(act("create_storage_bucket", bucket_name="new-bucket",
                                  environment="staging")) is None


def test_modify_and_delete_preconditions(model):
    assert model.check_action(act("enable_bucket_versioning",
                                  bucket_name="staging-assets-bucket")) is None
    err = model.check_action(act("delete_storage_bucket", bucket_name="ghost",
                                 environment="staging"))
    assert err.startswith("state_mismatch:unknown_resource")


def test_environment_mismatch_blocked(model):
    err = model.check_action(act("delete_storage_bucket",
                                 bucket_name="staging-assets-bucket",
                                 environment="production"))
    assert err.startswith("state_mismatch:environment")


def test_beliefs_track_executed_actions(model):
    model.apply_action(act("create_compute_instance", instance_name="dev-api-01",
                           size="small", environment="dev"))
    assert model.get("dev-api-01").attributes["size"] == "small"
    model.apply_action(act("delete_compute_instance", instance_id="dev-api-01",
                           environment="dev"))
    assert not model.exists("dev-api-01")


def test_diff_detects_drift_and_controls(model):
    drifted = {"staging-assets-bucket": {
        "resource_type": "storage_bucket", "resource_id": "staging-assets-bucket",
        "environment": "staging",
        "attributes": {"encryption": "disabled", "public_access": False}}}
    drifts = model.diff_desired(drifted)
    assert len(drifts) == 1
    assert drifts[0].attribute == "encryption"
    assert drifts[0].observed_value == "disabled"

    in_sync = {"staging-assets-bucket": {
        "resource_type": "storage_bucket", "resource_id": "staging-assets-bucket",
        "environment": "staging",
        "attributes": {"encryption": "enabled", "public_access": False}}}
    assert model.diff_desired(in_sync) == []


def test_refresh_overwrites_beliefs_keeps_desired(model):
    model.refresh({"staging-assets-bucket": {
        "resource_type": "storage_bucket", "resource_id": "staging-assets-bucket",
        "environment": "staging",
        "attributes": {"encryption": "disabled", "public_access": False}}})
    record = model.get("staging-assets-bucket")
    assert record.attributes["encryption"] == "disabled"
    assert record.desired_state == {"encryption": "enabled"}
