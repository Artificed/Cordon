import pytest

from cordon.dataset import load_dataset
from cordon.env import InMemoryEnvironment
from cordon.execution import ExecutionInterface, validate_action
from cordon.schema import Plan, PlannedAction, Resource


@pytest.fixture(scope="session")
def dataset():
    return load_dataset()


@pytest.fixture(scope="session")
def catalog(dataset):
    return {a.action_name: a for a in dataset.actions}


@pytest.fixture
def interface(dataset):
    return ExecutionInterface(dataset.actions, InMemoryEnvironment())


def act(name, **params):
    return PlannedAction(action_name=name, parameters=params)


# --- static validation -----------------------------------------------------

def test_unknown_action(catalog):
    assert validate_action(act("drop_all_tables"), catalog) == "unknown_action"


def test_missing_parameter(catalog):
    assert validate_action(act("create_storage_bucket", environment="staging"),
                           catalog) == "missing_parameter"


def test_null_parameter_counts_as_missing(catalog):
    assert validate_action(act("enable_bucket_encryption", bucket_name="b",
                               encryption_type=None), catalog) == "missing_parameter"


@pytest.mark.parametrize("bad_name", [
    "Staging_Assets!", "PROD-DATA", "a", "bkt__with__double__underscore",
    "toolongbucketname" * 5, "bucket name with spaces", "192.168.0.1",
    "-leadinghyphen", "trailinghyphen-", "UPPER-CASE-BUCKET",
])
def test_invalid_bucket_names(catalog, bad_name):
    """Every dataset invalid-name variant must fail static validation."""
    a = act("create_storage_bucket", bucket_name=bad_name, environment="staging")
    assert validate_action(a, catalog) == "invalid_resource_name"


def test_valid_bucket_name(catalog):
    a = act("create_storage_bucket", bucket_name="staging-assets-bucket",
            environment="staging")
    assert validate_action(a, catalog) is None


def test_invalid_size(catalog):
    a = act("create_compute_instance", instance_name="staging-api-01",
            size="nano-invalid", environment="staging")
    assert validate_action(a, catalog) == "invalid_parameter"


def test_invalid_environment(catalog):
    a = act("create_compute_instance", instance_name="x", size="small",
            environment="prod")
    assert validate_action(a, catalog) == "invalid_parameter"


def test_invalid_port_and_protocol(catalog):
    base = dict(security_group_id="sg", cidr="0.0.0.0/0")
    assert validate_action(act("update_firewall_rule", port=99999, protocol="tcp",
                               **base), catalog) == "invalid_parameter"
    assert validate_action(act("update_firewall_rule", port=22, protocol="carrier-pigeon",
                               **base), catalog) == "invalid_parameter"


def test_public_access_defaults_when_omitted(catalog):
    a = act("create_storage_bucket", bucket_name="dev-logs-bucket", environment="dev")
    assert validate_action(a, catalog) is None


# --- execution against the environment --------------------------------------

def test_two_action_plan_happy_path(interface):
    plan = Plan(intent_summary="encrypted staging bucket", actions=[
        act("create_storage_bucket", bucket_name="staging-assets-bucket",
            environment="staging"),
        act("enable_bucket_encryption", bucket_name="staging-assets-bucket",
            encryption_type="AES256"),
    ])
    executed, error = interface.execute_plan(plan)
    assert error is None
    assert executed == ["create_storage_bucket", "enable_bucket_encryption"]
    attrs = interface.env.get("staging-assets-bucket")["attributes"]
    assert attrs["encryption"] == "enabled"
    assert attrs["public_access"] is False


def test_name_conflict_via_precheck(interface):
    interface.env.load_initial_state([Resource(
        resource_type="storage_bucket", resource_id="staging-assets-bucket",
        environment="staging", attributes={"encryption": "enabled"})])
    executed, error = interface.execute_plan(Plan(actions=[
        act("create_storage_bucket", bucket_name="staging-assets-bucket",
            environment="staging")]))
    assert (executed, error) == ([], "name_conflict")


def test_halt_on_first_error_no_rollback(interface):
    plan = Plan(actions=[
        act("create_storage_bucket", bucket_name="dev-data-bucket", environment="dev"),
        act("enable_bucket_encryption", bucket_name="ghost-bucket",
            encryption_type="AES256"),
        act("enable_bucket_versioning", bucket_name="dev-data-bucket"),
    ])
    executed, error = interface.execute_plan(plan)
    assert executed == ["create_storage_bucket"]
    assert error == "not_found"
    # halt-and-report: the first action's effect remains (no rollback)
    assert interface.env.exists("dev-data-bucket")


def test_delete_missing_resource(interface):
    executed, error = interface.execute_plan(Plan(actions=[
        act("delete_compute_instance", instance_id="production-api-01",
            environment="production")]))
    assert (executed, error) == ([], "not_found")


def test_empty_plan_executes_nothing(interface):
    assert interface.execute_plan(Plan(actions=[])) == ([], None)
