"""LocalStack environment backend: real AWS APIs against an emulator.

Resource mapping (logical dataset concepts -> AWS):

- storage_bucket      -> S3 bucket (bucket name == resource_id); environment
                         via bucket tag; encryption via SSE config (explicitly
                         deleted at creation so buckets start unencrypted);
                         public_access via the PublicAccessBlock (all-blocked
                         == private); versioning via bucket versioning.
- compute_instance    -> EC2 instance; logical id + environment via tags;
                         logical size via a fixed instance-type map.
- security_group      -> EC2 security group (GroupName == resource_id);
                         ingress rules observed canonically as {port, cidr}.

The backend assumes it owns the LocalStack instance: cleanup() removes every
bucket, tagged instance, and non-default security group.
"""

from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError, ParamValidationError

from cordon.env.base import EnvironmentBackend
from cordon.schema import DriftSpec, PlannedAction, Resource

RESOURCE_ID_TAG = "cordon:resource_id"
ENVIRONMENT_TAG = "environment"

SIZE_TO_INSTANCE_TYPE = {
    "small": "t3.small",
    "medium": "t3.medium",
    "large": "t3.large",
    "xlarge": "t3.xlarge",
    "2xlarge": "t3.2xlarge",
    "4xlarge": "c5.4xlarge",
}
INSTANCE_TYPE_TO_SIZE = {v: k for k, v in SIZE_TO_INSTANCE_TYPE.items()}

LIVE_INSTANCE_STATES = ("pending", "running")


class LocalStackEnvironment(EnvironmentBackend):
    def __init__(self, endpoint_url: str = "http://localhost:4566",
                 region: str = "us-east-1") -> None:
        session = boto3.session.Session(
            aws_access_key_id="test", aws_secret_access_key="test", region_name=region,
        )
        boto_config = BotoConfig(retries={"max_attempts": 3, "mode": "standard"})
        self.s3 = session.client("s3", endpoint_url=endpoint_url, config=boto_config)
        self.ec2 = session.client("ec2", endpoint_url=endpoint_url, config=boto_config)
        self._ami: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def load_initial_state(self, resources: list[Resource]) -> None:
        self.cleanup()
        for r in resources:
            attrs = {k: v for k, v in r.attributes.items() if k != "desired_state"}
            if r.resource_type == "storage_bucket":
                self._create_bucket(r.resource_id, r.environment,
                                    public_access=bool(attrs.get("public_access", False)))
                if attrs.get("encryption") == "enabled":
                    self._put_encryption(r.resource_id, "AES256")
                if attrs.get("versioning"):
                    self._put_versioning(r.resource_id, enabled=True)
            elif r.resource_type == "compute_instance":
                self._run_instance(r.resource_id, attrs.get("size", "medium"), r.environment)
            elif r.resource_type == "security_group":
                self._create_security_group(r.resource_id, r.environment)
                for rule in attrs.get("ingress_rules", []):
                    self._authorize_ingress(r.resource_id, rule["port"],
                                            rule.get("protocol", "tcp"), rule["cidr"])
            else:
                raise ValueError(f"unknown resource_type: {r.resource_type}")

    def apply_drift(self, drift: DriftSpec) -> None:
        # Drift scenarios only target buckets; keep that assumption explicit.
        bucket = drift.resource_id
        if drift.attribute == "encryption" and drift.actual_value == "disabled":
            self.s3.delete_bucket_encryption(Bucket=bucket)
        elif drift.attribute == "versioning" and drift.actual_value is False:
            self._put_versioning(bucket, enabled=False)
        elif drift.attribute == "public_access" and drift.actual_value is True:
            self._put_public_access_block(bucket, public=True)
        else:
            raise ValueError(f"unsupported drift: {drift}")

    def cleanup(self) -> None:
        for bucket in self._list_bucket_names():
            self._delete_bucket(bucket)
        instance_ids = [i["InstanceId"] for i in self._live_instances()]
        if instance_ids:
            self.ec2.terminate_instances(InstanceIds=instance_ids)
        for sg in self.ec2.describe_security_groups()["SecurityGroups"]:
            if sg["GroupName"] != "default":
                self.ec2.delete_security_group(GroupId=sg["GroupId"])

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def exists(self, resource_id: str) -> bool:
        if self._bucket_exists(resource_id):
            return True
        if self._find_instance(resource_id) is not None:
            return True
        return self._find_security_group_id(resource_id) is not None

    def get(self, resource_id: str) -> dict[str, Any] | None:
        return self.observe().get(resource_id)

    def _bucket_exists(self, bucket: str) -> bool:
        try:
            self.s3.head_bucket(Bucket=bucket)
            return True
        except (ClientError, ParamValidationError):
            return False

    def observe(self) -> dict[str, dict[str, Any]]:
        snapshot: dict[str, dict[str, Any]] = {}
        for bucket in self._list_bucket_names():
            snapshot[bucket] = {
                "resource_type": "storage_bucket",
                "resource_id": bucket,
                "environment": self._bucket_environment(bucket),
                "attributes": {
                    "encryption": self._bucket_encryption(bucket),
                    "public_access": self._bucket_public_access(bucket),
                    "versioning": self._bucket_versioning(bucket),
                },
            }
        for instance in self._live_instances():
            tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
            rid = tags.get(RESOURCE_ID_TAG)
            if rid is None:
                continue
            instance_type = instance["InstanceType"]
            snapshot[rid] = {
                "resource_type": "compute_instance",
                "resource_id": rid,
                "environment": tags.get(ENVIRONMENT_TAG, "staging"),
                "attributes": {
                    "size": INSTANCE_TYPE_TO_SIZE.get(instance_type, instance_type),
                    "status": "running",
                },
            }
        for sg in self.ec2.describe_security_groups()["SecurityGroups"]:
            if sg["GroupName"] == "default":
                continue
            tags = {t["Key"]: t["Value"] for t in sg.get("Tags", [])}
            rules = [
                {"port": permission.get("FromPort", 0), "cidr": ip_range["CidrIp"]}
                for permission in sg.get("IpPermissions", [])
                for ip_range in permission.get("IpRanges", [])
            ]
            snapshot[sg["GroupName"]] = {
                "resource_type": "security_group",
                "resource_id": sg["GroupName"],
                "environment": tags.get(ENVIRONMENT_TAG, "staging"),
                "attributes": {"ingress_rules": rules},
            }
        return snapshot

    # ------------------------------------------------------------------
    # Mutations (assume validated + prechecked input from the interface)
    # ------------------------------------------------------------------

    def apply(self, action: PlannedAction) -> str | None:
        name, p = action.action_name, action.parameters
        try:
            if name == "create_storage_bucket":
                self._create_bucket(p["bucket_name"], p["environment"],
                                    public_access=bool(p.get("public_access", False)))
            elif name == "enable_bucket_encryption":
                self._put_encryption(p["bucket_name"], p["encryption_type"])
            elif name == "enable_bucket_versioning":
                self._put_versioning(p["bucket_name"], enabled=True)
            elif name == "set_bucket_public_access":
                self._put_public_access_block(p["bucket_name"], public=bool(p["public_access"]))
            elif name == "delete_storage_bucket":
                self._delete_bucket(p["bucket_name"])
            elif name == "create_compute_instance":
                self._run_instance(p["instance_name"], p["size"], p["environment"])
            elif name == "delete_compute_instance":
                instance = self._find_instance(p["instance_id"])
                if instance is None:
                    return "not_found"
                self.ec2.terminate_instances(InstanceIds=[instance["InstanceId"]])
            elif name == "delete_security_group":
                group_id = self._find_security_group_id(p["security_group_id"])
                if group_id is None:
                    return "not_found"
                self.ec2.delete_security_group(GroupId=group_id)
            elif name == "update_firewall_rule":
                if self._find_security_group_id(p["security_group_id"]) is None:
                    return "not_found"
                self._authorize_ingress(p["security_group_id"], p["port"],
                                        p.get("protocol", "tcp"), p["cidr"])
            elif name == "inspect_resource_state":
                if not self.exists(p["resource_id"]):
                    return "not_found"
            else:
                return "unknown_action"
        except ParamValidationError:
            return "invalid_resource_name"
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "ClientError")
            if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                return "name_conflict"
            if code in ("NoSuchBucket", "InvalidGroup.NotFound",
                        "InvalidInstanceID.NotFound"):
                return "not_found"
            if code in ("InvalidBucketName",):
                return "invalid_resource_name"
            return f"aws_error:{code}"
        return None

    # ------------------------------------------------------------------
    # S3 helpers
    # ------------------------------------------------------------------

    def _list_bucket_names(self) -> list[str]:
        return [b["Name"] for b in self.s3.list_buckets().get("Buckets", [])]

    def _create_bucket(self, bucket: str, environment: str, *, public_access: bool) -> None:
        self.s3.create_bucket(Bucket=bucket)
        self.s3.put_bucket_tagging(
            Bucket=bucket,
            Tagging={"TagSet": [{"Key": ENVIRONMENT_TAG, "Value": str(environment)}]},
        )
        # Buckets must start unencrypted: emulate pre-2023 S3 defaults so the
        # "encryption enabled" state is always an explicit act.
        try:
            self.s3.delete_bucket_encryption(Bucket=bucket)
        except ClientError:
            pass
        self._put_public_access_block(bucket, public=public_access)

    def _delete_bucket(self, bucket: str) -> None:
        objects = self.s3.list_objects_v2(Bucket=bucket).get("Contents", [])
        if objects:
            self.s3.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": o["Key"]} for o in objects]},
            )
        self.s3.delete_bucket(Bucket=bucket)

    def _put_encryption(self, bucket: str, encryption_type: str) -> None:
        self.s3.put_bucket_encryption(
            Bucket=bucket,
            ServerSideEncryptionConfiguration={"Rules": [{
                "ApplyServerSideEncryptionByDefault": {
                    "SSEAlgorithm": encryption_type if encryption_type == "AES256" else "aws:kms",
                },
            }]},
        )

    def _put_versioning(self, bucket: str, *, enabled: bool) -> None:
        self.s3.put_bucket_versioning(
            Bucket=bucket,
            VersioningConfiguration={"Status": "Enabled" if enabled else "Suspended"},
        )

    def _put_public_access_block(self, bucket: str, *, public: bool) -> None:
        blocked = not public
        self.s3.put_public_access_block(
            Bucket=bucket,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": blocked,
                "IgnorePublicAcls": blocked,
                "BlockPublicPolicy": blocked,
                "RestrictPublicBuckets": blocked,
            },
        )

    def _bucket_environment(self, bucket: str) -> str:
        try:
            tags = self.s3.get_bucket_tagging(Bucket=bucket)["TagSet"]
        except ClientError:
            return "staging"
        return next((t["Value"] for t in tags if t["Key"] == ENVIRONMENT_TAG), "staging")

    def _bucket_encryption(self, bucket: str) -> str:
        try:
            config = self.s3.get_bucket_encryption(Bucket=bucket)
        except ClientError:
            return "disabled"
        # LocalStack returns an empty configuration (no Rules key) where AWS
        # would raise ServerSideEncryptionConfigurationNotFoundError.
        rules = config.get("ServerSideEncryptionConfiguration", {}).get("Rules", [])
        return "enabled" if rules else "disabled"

    def _bucket_versioning(self, bucket: str) -> bool:
        status = self.s3.get_bucket_versioning(Bucket=bucket).get("Status")
        return status == "Enabled"

    def _bucket_public_access(self, bucket: str) -> bool:
        try:
            config = self.s3.get_public_access_block(Bucket=bucket)[
                "PublicAccessBlockConfiguration"]
        except ClientError:
            return True  # nothing blocked -> effectively public
        return not all(config.get(key, False) for key in (
            "BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy",
            "RestrictPublicBuckets"))

    # ------------------------------------------------------------------
    # EC2 helpers
    # ------------------------------------------------------------------

    def _default_ami(self) -> str:
        if self._ami is None:
            images = self.ec2.describe_images()["Images"]
            self._ami = images[0]["ImageId"] if images else "ami-cordon0000000000"
        return self._ami

    def _run_instance(self, resource_id: str, size: str, environment: str) -> None:
        self.ec2.run_instances(
            ImageId=self._default_ami(),
            InstanceType=SIZE_TO_INSTANCE_TYPE.get(size, size),
            MinCount=1, MaxCount=1,
            TagSpecifications=[{
                "ResourceType": "instance",
                "Tags": [
                    {"Key": RESOURCE_ID_TAG, "Value": resource_id},
                    {"Key": ENVIRONMENT_TAG, "Value": str(environment)},
                    {"Key": "Name", "Value": resource_id},
                ],
            }],
        )

    def _live_instances(self) -> list[dict[str, Any]]:
        reservations = self.ec2.describe_instances(Filters=[
            {"Name": "instance-state-name", "Values": list(LIVE_INSTANCE_STATES)},
        ])["Reservations"]
        return [i for r in reservations for i in r["Instances"]]

    def _find_instance(self, resource_id: str) -> dict[str, Any] | None:
        for instance in self._live_instances():
            tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
            if tags.get(RESOURCE_ID_TAG) == resource_id:
                return instance
        return None

    # ------------------------------------------------------------------
    # Security group helpers
    # ------------------------------------------------------------------

    def _create_security_group(self, resource_id: str, environment: str) -> None:
        self.ec2.create_security_group(
            GroupName=resource_id,
            Description=f"cordon experiment security group {resource_id}",
            TagSpecifications=[{
                "ResourceType": "security-group",
                "Tags": [
                    {"Key": RESOURCE_ID_TAG, "Value": resource_id},
                    {"Key": ENVIRONMENT_TAG, "Value": str(environment)},
                ],
            }],
        )

    def _find_security_group_id(self, resource_id: str) -> str | None:
        groups = self.ec2.describe_security_groups(Filters=[
            {"Name": "group-name", "Values": [resource_id]},
        ])["SecurityGroups"]
        return groups[0]["GroupId"] if groups else None

    def _authorize_ingress(self, resource_id: str, port: int, protocol: str, cidr: str) -> None:
        group_id = self._find_security_group_id(resource_id)
        if protocol == "all" or port == 0:
            permission: dict[str, Any] = {"IpProtocol": "-1"}
        else:
            permission = {"IpProtocol": protocol, "FromPort": port, "ToPort": port}
        permission["IpRanges"] = [{"CidrIp": cidr}]
        self.ec2.authorize_security_group_ingress(GroupId=group_id,
                                                  IpPermissions=[permission])
