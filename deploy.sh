#!/usr/bin/env bash
# Deploys SpikeGuard to your AWS account. Works in AWS CloudShell with no installs.
#   bash deploy.sh                     (no email alerts)
#   bash deploy.sh you@example.com     (also email alerts through SNS)
set -euo pipefail

STACK="${STACK:-spikeguard}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-south-1}}"
EMAIL="${1:-}"

echo "Region: $REGION   Stack: $STACK"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="spikeguard-artifacts-${ACCOUNT}-${REGION}"

if ! aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null; then
  echo "Creating S3 bucket for the code package: $BUCKET"
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
  fi
fi

echo "Packaging code..."
aws cloudformation package \
  --template-file template.yaml \
  --s3-bucket "$BUCKET" \
  --output-template-file packaged.yaml \
  --region "$REGION" >/dev/null

PARAMS=()
if [ -n "$EMAIL" ]; then PARAMS=(--parameter-overrides "AlertEmail=$EMAIL"); fi

echo "Deploying (this takes 1 to 3 minutes)..."
aws cloudformation deploy \
  --template-file packaged.yaml \
  --stack-name "$STACK" \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
  --region "$REGION" \
  ${PARAMS[@]+"${PARAMS[@]}"}

URL="$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='DashboardUrl'].OutputValue" --output text)"

echo
echo "Done. Your live dashboard:"
echo "  $URL"
echo
echo "Open it, click 'Load one hour of demo traffic', then try the scenario buttons."
if [ -n "$EMAIL" ]; then echo "Check $EMAIL and confirm the SNS subscription to receive alert emails."; fi
