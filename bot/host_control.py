"""Pluggable "stop the host" action for idle shutdown and /sleep.

Self-hosted/standalone installs have nothing sensible to do here -- the user
turns the app off themselves -- so NoopHostController is the default. The
legacy AWS deployment stops the EC2 instance it's running on; Ec2HostController
wraps ec2_control.py (and therefore boto3/IMDS) but only imports it inside
stop_host(), so building this module never requires EC2 to be reachable or
boto3 to even be importable at process startup. The hosted platform runs
each bot as an ECS service; EcsHostController scales that service to zero,
same lazy-import pattern.
"""

import logging
from typing import Protocol

log = logging.getLogger("host_control")


class HostController(Protocol):
    async def stop_host(self) -> None: ...
    def describe(self) -> str: ...


class NoopHostController:
    async def stop_host(self) -> None:
        log.info("stop_host() called with HOST_CONTROLLER=noop -- nothing to do")

    def describe(self) -> str:
        return "noop"


class Ec2HostController:
    async def stop_host(self) -> None:
        import asyncio

        import ec2_control

        await asyncio.to_thread(ec2_control.stop_this_instance)

    def describe(self) -> str:
        return "ec2"


class EcsHostController:
    """Sets this bot's own ECS service (ECS_CLUSTER/ECS_SERVICE) to
    desiredCount=0; ECS then stops the task. /wake sets it back to 1 from
    the interactions Lambda."""

    async def stop_host(self) -> None:
        import asyncio

        import boto3

        from config import AWS_REGION, ECS_CLUSTER, ECS_SERVICE

        client = boto3.client("ecs", region_name=AWS_REGION)
        log.warning("stopping: scaling ECS service %s/%s to 0", ECS_CLUSTER, ECS_SERVICE)
        await asyncio.to_thread(client.update_service, cluster=ECS_CLUSTER, service=ECS_SERVICE, desiredCount=0)

    def describe(self) -> str:
        return "ecs"


def build_host_controller(kind: str) -> HostController:
    if kind == "noop":
        return NoopHostController()
    if kind == "ec2":
        return Ec2HostController()
    if kind == "ecs":
        return EcsHostController()
    raise ValueError(f"unknown HOST_CONTROLLER: {kind!r} (expected 'noop', 'ec2' or 'ecs')")
