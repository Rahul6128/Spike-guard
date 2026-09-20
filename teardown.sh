#!/usr/bin/env bash
# Deletes everything SpikeGuard created, so nothing keeps running or costing money.
set -euo pipefail
STACK="${STACK:-spikeguard}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-south-1}}"
aws cloudformation delete-stack --stack-name "$STACK" --region "$REGION"
aws cloudformation wait stack-delete-complete --stack-name "$STACK" --region "$REGION"
echo "Stack deleted. (The small S3 bucket spikeguard-artifacts-* is left; delete it in the S3 console if you want.)"
