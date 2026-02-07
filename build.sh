#!/bin/bash
# build.sh - Build Docker image locally and push to AWS ECR
# Usage: ./build.sh [version]
# Example: ./build.sh v1.0.0

set -e  # Exit on error
set -u  # Exit on undefined variable
set -o pipefail  # Exit on pipe failure

# Configuration
ECR_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
REPOSITORY_NAME="polymarket-weather-bot"
VERSION="${1:-$(git rev-parse --short HEAD 2>/dev/null || echo 'local')}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
step() { echo -e "${BLUE}[STEP]${NC} $1"; }

info "Starting build process..."

# Verify required tools
step "Checking prerequisites..."

if ! command -v docker &> /dev/null; then
    error "Docker not found. Please install Docker first."
fi

if ! docker info &> /dev/null; then
    error "Docker is not running. Please start Docker."
fi

if ! command -v aws &> /dev/null; then
    error "AWS CLI not found. Please install AWS CLI."
fi

# Verify we're in the right directory
if [ ! -f "Dockerfile" ]; then
    error "Dockerfile not found. Run this script from the project root."
fi

if [ ! -f "requirements.txt" ]; then
    error "requirements.txt not found. Run this script from the project root."
fi

# Get AWS account ID
step "Authenticating with AWS..."
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) || \
    error "Failed to get AWS account ID. Check AWS credentials."

# Construct ECR repository URL
ECR_URL="${AWS_ACCOUNT_ID}.dkr.ecr.${ECR_REGION}.amazonaws.com/${REPOSITORY_NAME}"

info "Build Configuration:"
echo "  Region:    ${ECR_REGION}"
echo "  Account:   ${AWS_ACCOUNT_ID}"
echo "  Version:   ${VERSION}"
echo "  Timestamp: ${TIMESTAMP}"
echo "  ECR URL:   ${ECR_URL}"
echo ""

# Check if ECR repository exists
step "Verifying ECR repository..."
if ! aws ecr describe-repositories --repository-names ${REPOSITORY_NAME} --region ${ECR_REGION} &>/dev/null; then
    warn "ECR repository '${REPOSITORY_NAME}' not found!"
    echo "Run: ./setup-ecr.sh"
    error "ECR repository does not exist"
fi

# Authenticate with ECR
step "Authenticating with ECR..."
aws ecr get-login-password --region ${ECR_REGION} 2>/dev/null | \
    docker login --username AWS --password-stdin ${ECR_URL} &>/dev/null || \
    error "ECR authentication failed"

# Build the Docker image
step "Building Docker image..."
echo "This may take several minutes (installing numpy, scikit-learn, cryptography)..."
docker build \
    --build-arg VERSION=${VERSION} \
    --build-arg TIMESTAMP=${TIMESTAMP} \
    --platform linux/amd64 \
    -t ${REPOSITORY_NAME}:${VERSION} \
    -t ${REPOSITORY_NAME}:latest \
    . || error "Docker build failed"

# Tag images for ECR
step "Tagging images for ECR..."
docker tag ${REPOSITORY_NAME}:${VERSION} ${ECR_URL}:${VERSION}
docker tag ${REPOSITORY_NAME}:${VERSION} ${ECR_URL}:latest
docker tag ${REPOSITORY_NAME}:${VERSION} ${ECR_URL}:${TIMESTAMP}

# Push to ECR
step "Pushing images to ECR..."
docker push ${ECR_URL}:${VERSION} || error "Failed to push ${VERSION} tag"
docker push ${ECR_URL}:latest || error "Failed to push latest tag"
docker push ${ECR_URL}:${TIMESTAMP} || error "Failed to push timestamp tag"

# Clean up local images to save space (optional)
if [ "${CLEANUP:-false}" = "true" ]; then
    info "Cleaning up local images..."
    docker rmi ${REPOSITORY_NAME}:${VERSION} ${REPOSITORY_NAME}:latest 2>/dev/null || true
fi

echo ""
info "Build Complete!"
echo "  Images pushed to ECR:"
echo "    - ${ECR_URL}:${VERSION}"
echo "    - ${ECR_URL}:latest"
echo "    - ${ECR_URL}:${TIMESTAMP}"
echo ""
echo "Next steps:"
echo "  1. SSH to EC2:    ssh -i keys/Polymarket-ec2-test.pem ec2-user@<EC2-IP>"
echo "  2. Deploy:        ./deploy.sh ${VERSION}"
echo ""
echo "Or deploy latest: ./deploy.sh latest"

