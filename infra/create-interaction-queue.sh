#!/usr/bin/env bash
# One-time (per account/region) SQS queue that relays Discord interactions
# Lambda can't handle itself (anything besides /wake and /sleep) to the bot
# process running on EC2.
#
# Why this exists: Discord delivers interactions either over the gateway or
# to a single HTTP "Interactions Endpoint URL" -- never both. Once the Lambda
# Function URL is set as that endpoint (required for /wake to work while the
# instance is stopped), the bot's gateway connection stops receiving
# INTERACTION_CREATE events entirely, even for commands like /connect that
# only make sense while the bot is already running. So Lambda forwards
# anything that isn't wake/sleep onto this queue, and the bot long-polls it.
#
# Idempotent: safe to re-run.
#
# Optional env vars:
#   AWS_REGION      default us-east-2
#   QUEUE_NAME       default discord-music-bot-interactions
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-2}"
QUEUE_NAME="${QUEUE_NAME:-discord-music-bot-interactions}"

echo "== SQS queue =="
QUEUE_URL="$(aws sqs get-queue-url --queue-name "$QUEUE_NAME" --region "$AWS_REGION" --query QueueUrl --output text 2>/dev/null || true)"
if [ -z "$QUEUE_URL" ]; then
  QUEUE_URL="$(aws sqs create-queue \
    --queue-name "$QUEUE_NAME" \
    --attributes '{"MessageRetentionPeriod":"600","VisibilityTimeout":"30"}' \
    --region "$AWS_REGION" \
    --query QueueUrl --output text)"
  echo "-> created $QUEUE_NAME"
else
  echo "-> $QUEUE_NAME already exists"
fi

QUEUE_ARN="$(aws sqs get-queue-attributes \
  --queue-url "$QUEUE_URL" \
  --attribute-names QueueArn \
  --region "$AWS_REGION" \
  --query Attributes.QueueArn --output text)"

echo "== done =="
echo "Queue URL: $QUEUE_URL"
echo "Queue ARN: $QUEUE_ARN"
echo "-> pass this URL as INTERACTIONS_QUEUE_URL to infra/deploy-lambda.sh"
echo "-> set INTERACTIONS_QUEUE_URL=$QUEUE_URL in the bot's .env on the EC2 instance"
