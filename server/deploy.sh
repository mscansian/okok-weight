#!/bin/bash
set -e

IMAGE="${DEPLOY_IMAGE:?set DEPLOY_IMAGE to the ECR repository name}"
docker build --ssh default -t "$IMAGE" .

REGION="${DEPLOY_AWS_REGION:?set DEPLOY_AWS_REGION to the region of the ECR registry}"
ACCOUNT_ID="${DEPLOY_AWS_ACCOUNT_ID:?set DEPLOY_AWS_ACCOUNT_ID to the AWS account that owns the registry}"
PROFILE="${DEPLOY_AWS_PROFILE:?set DEPLOY_AWS_PROFILE to the AWS CLI profile that can push to the registry}"
ECR_ENDPOINT="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"

aws --profile "$PROFILE" ecr get-login-password --region "$REGION" \
| docker login --username AWS --password-stdin "$ECR_ENDPOINT"

docker tag "$IMAGE" "$ECR_ENDPOINT/$IMAGE"
docker push "$ECR_ENDPOINT/$IMAGE"
