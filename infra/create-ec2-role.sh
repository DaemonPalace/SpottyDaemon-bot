#!/usr/bin/env bash
# One-time (per account/region) setup of the IAM role + instance profile the
# EC2 instance runs as. Run this from your own machine (needs AWS creds),
# before or after launching the instance -- either way, attach the resulting
# instance profile to it (console: Actions -> Security -> Modify IAM role).
#
# Idempotent: safe to re-run to pick up policy changes.
#
# Optional env vars:
#   AWS_REGION      default us-east-2
#   ROLE_NAME        default discord-music-bot-instance-role
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-2}"
ROLE_NAME="${ROLE_NAME:-discord-music-bot-instance-role}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
TRUST_POLICY='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

echo "== IAM role =="
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document "$TRUST_POLICY" >/dev/null
  echo "-> created $ROLE_NAME"
else
  echo "-> $ROLE_NAME already exists"
fi

RESOLVED_POLICY="$(sed -e "s/REGION/$AWS_REGION/g" -e "s/ACCOUNT_ID/$ACCOUNT_ID/g" "$SCRIPT_DIR/iam-ec2-instance-policy.json")"
aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name discord-music-bot-ec2-self-access \
  --policy-document "$RESOLVED_POLICY" >/dev/null

echo "== instance profile =="
if ! aws iam get-instance-profile --instance-profile-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$ROLE_NAME" >/dev/null
  aws iam add-role-to-instance-profile \
    --instance-profile-name "$ROLE_NAME" \
    --role-name "$ROLE_NAME" >/dev/null
  echo "-> created instance profile $ROLE_NAME"
  echo "-> waiting for IAM propagation before it's attachable..."
  sleep 10
else
  echo "-> instance profile $ROLE_NAME already exists"
fi

echo "== done =="
echo "Attach with: aws ec2 associate-iam-instance-profile --instance-id <ID> --iam-instance-profile Name=$ROLE_NAME --region $AWS_REGION"
