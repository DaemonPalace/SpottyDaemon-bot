"""Discord Interactions Endpoint handler (deploy as a Lambda Function URL).

Registers as the bot's "Interactions Endpoint URL" in the Discord developer
portal. Discord delivers interactions either over the gateway or to this
single HTTP endpoint -- never both -- so once this is set, every slash
command (not just the ones below) is routed here instead of to the bot's
gateway connection.

Handled directly here (don't need the bot process running):
  /wake  -> ec2:StartInstances
  /sleep -> ec2:StopInstances

/connect and /link collect a password via a Discord modal popup rather than
a plain command argument, so it doesn't show up in the channel's visible
command-usage line. A modal has to be shown as the *immediate* response to
the slash command (Discord doesn't support deferring and showing a modal
later), so those two are answered here with a MODAL response instead of
being relayed. The modal's *submission* comes back to this same endpoint as
a separate MODAL_SUBMIT interaction -- that's what actually gets relayed to
the bot over SQS, alongside /disconnect, /link-finish, /link-web-api-finish,
/delete-slot, /jam and /play, which only make sense while the bot is
running. The bot long-polls the queue and sends the real response itself
via Discord's webhook-followup API using the interaction token.

The "Paste redirect URL" button on /link and /link-web-api's replies works
the same way: it's a message component click, but it also needs an
*immediate* MODAL response (same reasoning as above), so it's answered
directly here too instead of falling into the generic component handling
below -- see PASTE_URL_BUTTONS.

Message component interactions (button clicks) are deferred one of two
ways depending on what the eventual response needs to do: Jam's
Rewind/Play/Pause/Skip ("jam:<action>") as DEFERRED_UPDATE_MESSAGE, since
the bot's response edits the existing panel message; everything else
(currently /play's per-track Play/Queue buttons) as
DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE, since those post a fresh ephemeral
confirmation and leave the original message alone.

Autocomplete interactions (/play's live search-as-you-type) can't be
deferred at all -- Discord requires an immediate response -- and this
function has no access to a slot's cached Spotify token to do a real
search, so these are always answered with an empty choice list. /play
still works when relayed; it just has no live suggestions while typing.

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

# Discord interaction types (the incoming request).
TYPE_PING = 1
TYPE_APPLICATION_COMMAND = 2
TYPE_MESSAGE_COMPONENT = 3
TYPE_APPLICATION_COMMAND_AUTOCOMPLETE = 4
TYPE_MODAL_SUBMIT = 5

# Discord interaction response types (what we send back).
RESPONSE_PONG = 1
RESPONSE_CHANNEL_MESSAGE_WITH_SOURCE = 4
RESPONSE_DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE = 5
RESPONSE_DEFERRED_UPDATE_MESSAGE = 6
RESPONSE_MODAL = 9
RESPONSE_AUTOCOMPLETE_RESULT = 8

EPHEMERAL = 64
DIRECT_COMMANDS = {"wake", "sleep"}
# These are answered with a MODAL directly instead of being relayed -- see
# module docstring. Everything else that needs the bot running (disconnect,
# link-finish, delete-slot, and any MODAL_SUBMIT) goes over SQS as before.
MODAL_COMMANDS = {"connect", "reconnect", "link"}
# Button custom_ids that need an immediate MODAL response -- see module
# docstring. Maps the button's custom_id to (modal custom_id, title).
PASTE_URL_BUTTONS = {
    "paste:link": ("paste-finish:link", "Finish linking Spotify"),
    "paste:link-web-api": ("paste-finish:link-web-api", "Finish Web API link"),
}


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


def _message_response(content: str, ephemeral: bool = False) -> dict:
    data = {"content": content}
    if ephemeral:
        data["flags"] = EPHEMERAL
    return _response(200, {"type": RESPONSE_CHANNEL_MESSAGE_WITH_SOURCE, "data": data})


def _relay(body: dict, response_type: int = RESPONSE_DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE) -> dict:
    sqs = boto3.client("sqs", region_name=AWS_REGION)
    sqs.send_message(QueueUrl=INTERACTIONS_QUEUE_URL, MessageBody=json.dumps(body))
    return _response(200, {"type": response_type})


def _text_modal_response(
    custom_id: str, title: str, field_custom_id: str, label: str, required: bool, max_length: int
) -> dict:
    return _response(
        200,
        {
            "type": RESPONSE_MODAL,
            "data": {
                "custom_id": custom_id,
                "title": title,
                "components": [
                    {
                        "type": 1,
                        "components": [
                            {
                                "type": 4,
                                "custom_id": field_custom_id,
                                "label": label,
                                "style": 1,
                                "required": required,
                                "max_length": max_length,
                            }
                        ],
                    }
                ],
            },
        },
    )


def _password_modal_response(custom_id: str, title: str) -> dict:
    return _text_modal_response(custom_id, title, "password", "Password", required=True, max_length=100)


def _paste_url_modal_response(custom_id: str, title: str) -> dict:
    return _text_modal_response(
        custom_id, title, "url", "Redirect URL (blank if the page loaded)", required=False, max_length=500
    )


def handler(event, context):
    if not _verify_signature(event):
        return {"statusCode": 401, "body": "invalid request signature"}

    body = json.loads(event.get("body") or "{}")
    interaction_type = body.get("type")

    if interaction_type == TYPE_PING:
        return _response(200, {"type": RESPONSE_PONG})

    if interaction_type == TYPE_APPLICATION_COMMAND_AUTOCOMPLETE:
        # No EC2/instance lookup needed -- answer instantly, before that
        # budget gets eaten by an AWS API call on every keystroke.
        return _response(200, {"type": RESPONSE_AUTOCOMPLETE_RESULT, "data": {"choices": []}})

    if interaction_type not in (TYPE_APPLICATION_COMMAND, TYPE_MODAL_SUBMIT, TYPE_MESSAGE_COMPONENT):
        return _response(400, {"error": "unhandled interaction type"})

    command_name = body["data"]["name"] if interaction_type == TYPE_APPLICATION_COMMAND else None

    ec2 = boto3.client("ec2", region_name=AWS_REGION)
    instance_id, instance_state = _find_instance(ec2)
    if instance_id is None:
        return _message_response(f"No instance tagged Name={INSTANCE_TAG_NAME} found.", ephemeral=True)

    if interaction_type == TYPE_APPLICATION_COMMAND and command_name == "wake":
        ec2.start_instances(InstanceIds=[instance_id])
        return _message_response("Waking up the music bot instance… give it ~30s.")

    if interaction_type == TYPE_APPLICATION_COMMAND and command_name == "sleep":
        ec2.stop_instances(InstanceIds=[instance_id])
        return _message_response("Stopping the music bot instance.")

    if instance_state != "running":
        return _message_response(
            "The instance is asleep — run /wake first, then try again once it's up.",
            ephemeral=True,
        )

    if interaction_type == TYPE_APPLICATION_COMMAND and command_name in MODAL_COMMANDS:
        options = {opt["name"]: opt["value"] for opt in body["data"].get("options", [])}
        if command_name in ("connect", "reconnect"):
            slot_value = options.get("slot", "")
            return _password_modal_response(
                f"{command_name}:{slot_value}", f"Password for '{slot_value}'"
            )
        slotname_value = options.get("slotname", "")
        return _password_modal_response(f"link:{slotname_value}", f"Set a password for '{slotname_value}'")

    if interaction_type == TYPE_MESSAGE_COMPONENT:
        custom_id = body["data"]["custom_id"]
        if custom_id in PASTE_URL_BUTTONS:
            modal_custom_id, title = PASTE_URL_BUTTONS[custom_id]
            return _paste_url_modal_response(modal_custom_id, title)
        if custom_id.startswith("jam:"):
            # Jam's transport buttons -- the eventual response edits the
            # message the button lives on, not a new one.
            return _relay(body, response_type=RESPONSE_DEFERRED_UPDATE_MESSAGE)
        # Everything else (e.g. /play's per-track Play/Queue buttons) posts
        # a fresh response instead of editing the message it came from.
        return _relay(body, response_type=RESPONSE_DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE)

    # Everything else that reaches here (disconnect, link-finish,
    # link-web-api-finish, delete-slot, jam, play, and every MODAL_SUBMIT)
    # needs the bot process itself -- relay it and let the bot reply via
    # the interaction-followup webhook once it's done.
    return _relay(body)
