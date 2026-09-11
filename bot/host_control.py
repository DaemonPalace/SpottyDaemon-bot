"""Pluggable "stop the host" action for idle shutdown and /sleep.

Self-hosted/standalone installs have nothing sensible to do here -- the user
turns the app off themselves -- so NoopHostController is the default. The
legacy AWS deployment stops the EC2 instance it's running on; Ec2HostController
wraps ec2_control.py (and therefore boto3/IMDS) but only imports it inside
stop_host(), so building this module never requires EC2 to be reachable or
boto3 to even be importable at process startup.
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


def build_host_controller(kind: str) -> HostController:
    if kind == "noop":
        return NoopHostController()
    if kind == "ec2":
        return Ec2HostController()
    raise ValueError(f"unknown HOST_CONTROLLER: {kind!r} (expected 'noop' or 'ec2')")
