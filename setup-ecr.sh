#!/bin/bash
# setup-ecr.sh - Create ECR repository for the weather bot
# Run this once to set up the ECR repository

set -e

ECR_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
REPOSITORY_NAME="polymarket-weather-bot"

echo "=== Setting up ECR Repository ==="
echo "Region: ${ECR_REGION}"
echo "Repository: ${REPOSITORY_NAME}"

# Get AWS account ID
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Check if repository already exists
if aws ecr describe-repositories --repository-names ${REPOSITORY_NAME} --region ${ECR_REGION} 2>/dev/null; then
    echo "Repository ${REPOSITORY_NAME} already exists"
else
    echo "Creating ECR repository..."
    aws ecr create-repository \
        --repository-name ${REPOSITORY_NAME} \
        --region ${ECR_REGION} \
        --image-scanning-configuration scanOnPush=true \
        --image-tag-mutability MUTABLE
    
    echo "Repository created successfully"
fi

# Output the full repository URL
ECR_URL="${AWS_ACCOUNT_ID}.dkr.ecr.${ECR_REGION}.amazonaws.com/${REPOSITORY_NAME}"
echo ""
echo "=== Repository URL ==="
echo ${ECR_URL}
echo ""
echo "=== Next Steps ==="
echo "1. Run ./build.sh to build and push your first image"
echo "2. Copy deploy.sh to your EC2 instance"
echo "3. On EC2, run: ./deploy.sh latest"

