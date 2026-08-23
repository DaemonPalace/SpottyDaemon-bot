"""Discord Interactions Endpoint handler (deploy as a Lambda Function URL).

Registers as the bot's "Interactions Endpoint URL" in the Discord developer
portal. Discord delivers interactions either over the gateway or to this
single HTTP endpoint -- never both -- so once this is set, every slash
command (not just the ones below) is routed here instead of to the bot's
gateway connection.

Handled directly here (don't need the bot process running):
  /wake  -> ec2:StartInstances
  /sleep -> ec2:StopInstances

Everything else (e.g. /connect, /disconnect) only makes sense while the bot
is actually running, so those are deferred and relayed to the bot over SQS
-- the bot long-polls the queue and sends the real response itself via
Discord's webhook-followup API using the interaction token.

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
INTERACTIONS_QUEUE_URL = os.environ["INTERACTIONS_QUEUE_URL"]
AWS_REGION = os.environ.get("AWS_REGION")

_verify_key = VerifyKey(bytes.fromhex(DISCORD_PUBLIC_KEY))

PING = 1
PONG = 1
APPLICATION_COMMAND = 2
CHANNEL_MESSAGE_WITH_SOURCE = 4
DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE = 5
EPHEMERAL = 64
DIRECT_COMMANDS = {"wake", "sleep"}


def _find_instance(ec2) -> tuple[str, str] | tuple[None, None]:
    resp = ec2.describe_instances(
        Filters=[{"Name": "tag:Name", "Values": [INSTANCE_TAG_NAME]}]
    )
    for reservation in resp["Reservations"]:
        for instance in reservation["Instances"]:
            return instance["InstanceId"], instance["State"]["Name"]
    return None, None


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
        instance_id, instance_state = _find_instance(ec2)

        ephemeral = False
        if instance_id is None:
            message = f"No instance tagged Name={INSTANCE_TAG_NAME} found."
            ephemeral = True
        elif command_name == "wake":
            ec2.start_instances(InstanceIds=[instance_id])
            message = "Waking up the music bot instance… give it ~30s."
        elif command_name == "sleep":
            ec2.stop_instances(InstanceIds=[instance_id])
            message = "Stopping the music bot instance."
        elif command_name not in DIRECT_COMMANDS and instance_state != "running":
            message = "The instance is asleep — run /wake first, then try again once it's up."
            ephemeral = True
        else:
            # Not ours to handle -- relay to the bot over SQS and let it
            # reply via the interaction-followup webhook once it's done.
            sqs = boto3.client("sqs", region_name=AWS_REGION)
            sqs.send_message(QueueUrl=INTERACTIONS_QUEUE_URL, MessageBody=json.dumps(body))
            return _response(200, {"type": DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE})

        data = {"content": message}
        if ephemeral:
            data["flags"] = EPHEMERAL
        return _response(200, {"type": CHANNEL_MESSAGE_WITH_SOURCE, "data": data})

    return _response(400, {"error": "unhandled interaction type"})
