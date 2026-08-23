"""Self-stop this EC2 instance using IMDSv2 for identity + a scoped IAM role."""

import logging

import boto3
import urllib.request

log = logging.getLogger("ec2_control")

_METADATA_BASE = "http://169.254.169.254/latest"


def _imds_token() -> str:
    req = urllib.request.Request(
        f"{_METADATA_BASE}/api/token",
        method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
    )
    with urllib.request.urlopen(req, timeout=2) as resp:
        return resp.read().decode()


def get_instance_id() -> str:
    token = _imds_token()
    req = urllib.request.Request(
        f"{_METADATA_BASE}/meta-data/instance-id",
        headers={"X-aws-ec2-metadata-token": token},
    )
    with urllib.request.urlopen(req, timeout=2) as resp:
        return resp.read().decode()


def stop_this_instance(region: str | None = None) -> None:
    instance_id = get_instance_id()
    client = boto3.client("ec2", region_name=region)
    log.warning("idle timeout reached -> stopping instance %s", instance_id)
    client.stop_instances(InstanceIds=[instance_id])
