import pytest

from cordon.env import InMemoryEnvironment
from cordon.schema import DriftSpec, PlannedAction, Resource


def act(name, **params):
    return PlannedAction(action_name=name, parameters=params)


def bucket(rid="staging-assets-bucket", env="staging", **attrs):
    defaults = {"encryption": "enabled", "public_access": False}
    return Resource(
        resource_type="storage_bucket", resource_id=rid, environment=env,
        attributes=defaults | attrs,
    )


@pytest.fixture
def env():
    return InMemoryEnvironment()


def test_load_and_observe_are_isolated_copies(env):
    env.load_initial_state([bucket()])
    snapshot = env.observe()
    snapshot["staging-assets-bucket"]["attributes"]["encryption"] = "tampered"
    assert env.get("staging-assets-bucket")["attributes"]["encryption"] == "enabled"


def test_create_bucket_defaults(env):
    assert env.apply(act("create_storage_bucket", bucket_name="dev-logs-bucket",
                         environment="dev", public_access=False)) is None
    created = env.get("dev-logs-bucket")
    assert created["attributes"] == {"encryption": "disabled", "public_access": False}


def test_create_conflict(env):
    env.load_initial_state([bucket()])
    err = env.apply(act("create_storage_bucket", bucket_name="staging-assets-bucket",
                        environment="staging"))
    assert err == "name_conflict"


def test_bucket_attribute_actions(env):
    env.load_initial_state([bucket(encryption="disabled", public_access=True)])
    assert env.apply(act("enable_bucket_encryption", bucket_name="staging-assets-bucket",
                         encryption_type="AES256")) is None
    assert env.apply(act("enable_bucket_versioning", bucket_name="staging-assets-bucket")) is None
    assert env.apply(act("set_bucket_public_access", bucket_name="staging-assets-bucket",
                         public_access=False)) is None
    attrs = env.get("staging-assets-bucket")["attributes"]
    assert attrs["encryption"] == "enabled"
    assert attrs["versioning"] is True
    assert attrs["public_access"] is False


def test_modify_missing_resource(env):
    assert env.apply(act("enable_bucket_encryption", bucket_name="ghost",
                         encryption_type="AES256")) == "not_found"
    assert env.apply(act("delete_storage_bucket", bucket_name="ghost",
                         environment="staging")) == "not_found"
    assert env.apply(act("inspect_resource_state", resource_type="storage_bucket",
                         resource_id="ghost")) == "not_found"


def test_instance_lifecycle(env):
    assert env.apply(act("create_compute_instance", instance_name="staging-api-01",
                         size="medium", environment="staging")) is None
    assert env.get("staging-api-01")["attributes"] == {"size": "medium", "status": "running"}
    assert env.apply(act("delete_compute_instance", instance_id="staging-api-01",
                         environment="staging")) is None
    assert not env.exists("staging-api-01")


def test_firewall_rule_appends(env):
    env.load_initial_state([Resource(resource_type="security_group",
                                     resource_id="staging-api-sg", environment="staging",
                                     attributes={"ingress_rules": []})])
    assert env.apply(act("update_firewall_rule", security_group_id="staging-api-sg",
                         port=443, protocol="tcp", cidr="10.0.0.0/8")) is None
    rules = env.get("staging-api-sg")["attributes"]["ingress_rules"]
    assert rules == [{"port": 443, "protocol": "tcp", "cidr": "10.0.0.0/8"}]


def test_apply_drift_diverges_environment_only(env):
    env.load_initial_state([bucket()])
    env.apply_drift(DriftSpec(resource_id="staging-assets-bucket", attribute="encryption",
                              desired_value="enabled", actual_value="disabled"))
    assert env.get("staging-assets-bucket")["attributes"]["encryption"] == "disabled"


def test_cleanup(env):
    env.load_initial_state([bucket()])
    env.cleanup()
    assert env.observe() == {}
