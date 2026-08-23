"""Discord Interactions Endpoint handler (deploy as a Lambda Function URL).

Registers as the bot's "Interactions Endpoint URL" in the Discord developer
portal. Handles two slash commands:
  /wake  -> ec2:StartInstances
  /sleep -> ec2:StopInstances

Discord requires responding to the PING verification handshake and to every
interaction within 3 seconds with a valid Ed25519-signed response.
"""

import json
import os

import boto3
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

DISCORD_PUBLIC_KEY = os.environ["DISCORD_PUBLIC_KEY"]
INSTANCE_TAG_NAME = os.environ.get("INSTANCE_TAG_NAME", "discord-music-bot")
AWS_REGION = os.environ.get("AWS_REGION")

_verify_key = VerifyKey(bytes.fromhex(DISCORD_PUBLIC_KEY))

PING = 1
PONG = 1
APPLICATION_COMMAND = 2
CHANNEL_MESSAGE_WITH_SOURCE = 4


def _find_instance_id(ec2) -> str | None:
    resp = ec2.describe_instances(
        Filters=[{"Name": "tag:Name", "Values": [INSTANCE_TAG_NAME]}]
    )
    for reservation in resp["Reservations"]:
        for instance in reservation["Instances"]:
            return instance["InstanceId"]
    return None


def _verify_signature(event: dict) -> bool:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    signature = headers.get("x-signature-ed25519")
    timestamp = headers.get("x-signature-timestamp")
    body = event.get("body") or ""
    if not signature or not timestamp:
        return False
    try:
        _verify_key.verify(f"{timestamp}{body}".encode(), bytes.fromhex(signature))
        return True
    except BadSignatureError:
        return False


def _response(status: int, payload: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }


def handler(event, context):
    if not _verify_signature(event):
        return {"statusCode": 401, "body": "invalid request signature"}

    body = json.loads(event.get("body") or "{}")

    if body.get("type") == PING:
        return _response(200, {"type": PONG})

    if body.get("type") == APPLICATION_COMMAND:
        command_name = body["data"]["name"]
        ec2 = boto3.client("ec2", region_name=AWS_REGION)
        instance_id = _find_instance_id(ec2)

        if instance_id is None:
            message = f"No instance tagged Name={INSTANCE_TAG_NAME} found."
        elif command_name == "wake":
            ec2.start_instances(InstanceIds=[instance_id])
            message = "Waking up the music bot instance… give it ~30s."
        elif command_name == "sleep":
            ec2.stop_instances(InstanceIds=[instance_id])
            message = "Stopping the music bot instance."
        else:
            message = f"Unknown command: {command_name}"

        return _response(
            200,
            {"type": CHANNEL_MESSAGE_WITH_SOURCE, "data": {"content": message}},
        )

    return _response(400, {"error": "unhandled interaction type"})
