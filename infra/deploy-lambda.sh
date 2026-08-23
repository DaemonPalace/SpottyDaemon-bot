#!/usr/bin/env bash
# Build and deploy lambda/wake_sleep.py as a public Lambda Function URL.
#
# Idempotent: safe to re-run after code changes, or from scratch on a new
# account/region. Creates the IAM role + Function URL if missing, otherwise
# updates code/config in place.
#
# Required env vars:
#   DISCORD_PUBLIC_KEY   from the Discord developer portal (General Information page)
# Optional:
#   AWS_REGION            default us-east-2
#   FUNCTION_NAME          default discord-music-bot-wake-sleep
#   ROLE_NAME               default discord-music-bot-lambda-role
#   INSTANCE_TAG_NAME     default discord-music-bot
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-2}"
FUNCTION_NAME="${FUNCTION_NAME:-discord-music-bot-wake-sleep}"
ROLE_NAME="${ROLE_NAME:-discord-music-bot-lambda-role}"
INSTANCE_TAG_NAME="${INSTANCE_TAG_NAME:-discord-music-bot}"
DISCORD_PUBLIC_KEY="${DISCORD_PUBLIC_KEY:?set DISCORD_PUBLIC_KEY (Discord developer portal -> General Information)}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
LAMBDA_SRC="$REPO_ROOT/lambda"
BUILD_DIR="$(mktemp -d)"
ZIP_PATH="$LAMBDA_SRC/wake_sleep.zip"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

trap 'rm -rf "$BUILD_DIR"' EXIT

echo "== build deployment package =="
pip install -q -r "$LAMBDA_SRC/requirements.txt" -t "$BUILD_DIR" --platform manylinux2014_x86_64 --python-version 3.12 --only-binary=:all:
cp "$LAMBDA_SRC/wake_sleep.py" "$BUILD_DIR/"
(cd "$BUILD_DIR" && zip -qr "$ZIP_PATH" .)
echo "-> $ZIP_PATH"

echo "== IAM role =="
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document "file://$SCRIPT_DIR/lambda-trust-policy.json" >/dev/null
  echo "-> created $ROLE_NAME"
else
  echo "-> $ROLE_NAME already exists"
fi

aws iam attach-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole >/dev/null

sed -e "s/REGION/$AWS_REGION/g" -e "s/ACCOUNT_ID/$ACCOUNT_ID/g" "$SCRIPT_DIR/iam-lambda-policy.json" > "$BUILD_DIR/lambda-policy-resolved.json"
aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name discord-music-bot-ec2-access \
  --policy-document "file://$BUILD_DIR/lambda-policy-resolved.json" >/dev/null
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

echo "== function =="
if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
  aws lambda update-function-code \
    --function-name "$FUNCTION_NAME" \
    --zip-file "fileb://$ZIP_PATH" \
    --region "$AWS_REGION" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$AWS_REGION"
  aws lambda update-function-configuration \
    --function-name "$FUNCTION_NAME" \
    --environment "Variables={DISCORD_PUBLIC_KEY=$DISCORD_PUBLIC_KEY,INSTANCE_TAG_NAME=$INSTANCE_TAG_NAME}" \
    --region "$AWS_REGION" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$AWS_REGION"
  echo "-> updated $FUNCTION_NAME"
else
  # IAM role propagation can lag a few seconds after creation -- retry create.
  for attempt in 1 2 3 4 5; do
    if aws lambda create-function \
      --function-name "$FUNCTION_NAME" \
      --runtime python3.12 \
      --handler wake_sleep.handler \
      --role "$ROLE_ARN" \
      --timeout 10 \
      --memory-size 128 \
      --zip-file "fileb://$ZIP_PATH" \
      --environment "Variables={DISCORD_PUBLIC_KEY=$DISCORD_PUBLIC_KEY,INSTANCE_TAG_NAME=$INSTANCE_TAG_NAME}" \
      --region "$AWS_REGION" >/dev/null 2>&1; then
      break
    fi
    [ "$attempt" -eq 5 ] && { echo "create-function failed after retries"; exit 1; }
    sleep 5
  done
  aws lambda wait function-active --function-name "$FUNCTION_NAME" --region "$AWS_REGION"
  echo "-> created $FUNCTION_NAME"
fi

echo "== function url =="
if ! aws lambda get-function-url-config --function-name "$FUNCTION_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
  aws lambda create-function-url-config \
    --function-name "$FUNCTION_NAME" \
    --auth-type NONE \
    --region "$AWS_REGION" >/dev/null
  echo "-> created function url"
fi
FUNCTION_URL="$(aws lambda get-function-url-config --function-name "$FUNCTION_NAME" --region "$AWS_REGION" --query FunctionUrl --output text)"

echo "== public invoke permissions =="
# Both statements are required (AWS changed this in Oct 2025): AuthType=NONE
# alone is not enough -- the resource policy must separately grant
# lambda:InvokeFunctionUrl (the URL front door) AND plain lambda:InvokeFunction
# (the actual invoke), each for principal "*". Missing either -> 403
# AccessDeniedException from the Function URL before your code ever runs.
aws lambda add-permission \
  --function-name "$FUNCTION_NAME" \
  --statement-id AllowPublicFunctionUrlInvoke \
  --action lambda:InvokeFunctionUrl \
  --principal "*" \
  --function-url-auth-type NONE \
  --region "$AWS_REGION" >/dev/null 2>&1 || true
aws lambda add-permission \
  --function-name "$FUNCTION_NAME" \
  --statement-id AllowPublicFunctionInvoke \
  --action lambda:InvokeFunction \
  --principal "*" \
  --region "$AWS_REGION" >/dev/null 2>&1 || true

echo "== done =="
echo "Function URL: $FUNCTION_URL"
echo "-> paste this into the Discord developer portal's Interactions Endpoint URL field"
