"""LocalStack backend integration tests. Auto-skipped when LocalStack is down.

Start it with: docker compose up -d
"""

import urllib.request

import pytest

from cordon.env.localstack import LocalStackEnvironment
from cordon.execution import ExecutionInterface
from cordon.dataset import load_dataset
from cordon.schema import DriftSpec, Plan, PlannedAction, Resource


def localstack_running() -> bool:
    try:
        urllib.request.urlopen("http://localhost:4566/_localstack/health", timeout=2)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not localstack_running(),
                                reason="LocalStack is not running on :4566")


def act(name, **params):
    return PlannedAction(action_name=name, parameters=params)


@pytest.fixture(scope="module")
def env():
    backend = LocalStackEnvironment()
    yield backend
    backend.cleanup()


@pytest.fixture(autouse=True)
def clean(env):
    env.cleanup()


def bucket_resource(rid="staging-assets-bucket", **attrs):
    defaults = {"encryption": "enabled", "public_access": False}
    return Resource(resource_type="storage_bucket", resource_id=rid,
                    environment="staging", attributes=defaults | attrs)


def test_bucket_lifecycle(env):
    assert env.apply(act("create_storage_bucket", bucket_name="dev-data-bucket",
                         environment="dev", public_access=False)) is None
    observed = env.get("dev-data-bucket")
    assert observed["environment"] == "dev"
    assert observed["attributes"]["encryption"] == "disabled"  # explicit act required
    assert observed["attributes"]["public_access"] is False
    assert observed["attributes"]["versioning"] is False

    assert env.apply(act("enable_bucket_encryption", bucket_name="dev-data-bucket",
                         encryption_type="AES256")) is None
    assert env.apply(act("enable_bucket_versioning", bucket_name="dev-data-bucket")) is None
    assert env.apply(act("set_bucket_public_access", bucket_name="dev-data-bucket",
                         public_access=True)) is None
    attrs = env.get("dev-data-bucket")["attributes"]
    assert attrs == {"encryption": "enabled", "public_access": True, "versioning": True}

    assert env.apply(act("delete_storage_bucket", bucket_name="dev-data-bucket",
                         environment="dev")) is None
    assert not env.exists("dev-data-bucket")


def test_initial_state_and_drift(env):
    env.load_initial_state([bucket_resource(versioning=True)])
    attrs = env.get("staging-assets-bucket")["attributes"]
    assert attrs["encryption"] == "enabled" and attrs["versioning"] is True

    env.apply_drift(DriftSpec(resource_id="staging-assets-bucket", attribute="encryption",
                              desired_value="enabled", actual_value="disabled"))
    assert env.get("staging-assets-bucket")["attributes"]["encryption"] == "disabled"

    env.apply_drift(DriftSpec(resource_id="staging-assets-bucket", attribute="public_access",
                              desired_value=False, actual_value=True))
    assert env.get("staging-assets-bucket")["attributes"]["public_access"] is True


def test_instance_lifecycle(env):
    assert env.apply(act("create_compute_instance", instance_name="staging-api-01",
                         size="medium", environment="staging")) is None
    observed = env.get("staging-api-01")
    assert observed["attributes"] == {"size": "medium", "status": "running"}
    assert observed["environment"] == "staging"

    assert env.apply(act("delete_compute_instance", instance_id="staging-api-01",
                         environment="staging")) is None
    assert not env.exists("staging-api-01")


def test_security_group_rules(env):
    env.load_initial_state([Resource(
        resource_type="security_group", resource_id="production-api-sg",
        environment="production",
        attributes={"ingress_rules": [{"port": 443, "cidr": "10.0.0.0/8"}]})])
    assert env.get("production-api-sg")["attributes"]["ingress_rules"] == [
        {"port": 443, "cidr": "10.0.0.0/8"}]

    assert env.apply(act("update_firewall_rule", security_group_id="production-api-sg",
                         port=22, protocol="tcp", cidr="0.0.0.0/0")) is None
    rules = env.get("production-api-sg")["attributes"]["ingress_rules"]
    assert {"port": 22, "cidr": "0.0.0.0/0"} in rules

    assert env.apply(act("update_firewall_rule", security_group_id="production-api-sg",
                         port=0, protocol="all", cidr="0.0.0.0/0")) is None
    rules = env.get("production-api-sg")["attributes"]["ingress_rules"]
    assert {"port": 0, "cidr": "0.0.0.0/0"} in rules


def test_execution_interface_end_to_end(env):
    dataset = load_dataset()
    interface = ExecutionInterface(dataset.actions, env)
    executed, error = interface.execute_plan(Plan(actions=[
        act("create_storage_bucket", bucket_name="staging-e2e-bucket",
            environment="staging"),
        act("enable_bucket_encryption", bucket_name="staging-e2e-bucket",
            encryption_type="aws:kms"),
    ]))
    assert error is None
    assert executed == ["create_storage_bucket", "enable_bucket_encryption"]
    assert env.get("staging-e2e-bucket")["attributes"]["encryption"] == "enabled"

    # conflict caught by the interface pre-check against the live environment
    executed, error = interface.execute_plan(Plan(actions=[
        act("create_storage_bucket", bucket_name="staging-e2e-bucket",
            environment="staging")]))
    assert (executed, error) == ([], "name_conflict")


def test_cleanup_empties_everything(env):
    env.load_initial_state([bucket_resource()])
    env.apply(act("create_compute_instance", instance_name="dev-worker-01",
                  size="small", environment="dev"))
    env.cleanup()
    assert env.observe() == {}
