#!/usr/bin/env bash
# GUARDIAN deploy script — SSH-based deploy to AWS EC2
# Usage: ./scripts/deploy.sh <EC2_HOST> [<KEY_FILE>]
set -euo pipefail

EC2_HOST="${1:?Usage: $0 <ec2-host> [key-file]}"
KEY_FILE="${2:-~/.ssh/id_rsa}"
REPO_URL="https://github.com/guardian-ai/guardian.git"
REMOTE_DIR="/opt/guardian"

echo "▶ Deploying GUARDIAN to $EC2_HOST …"

ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no "ubuntu@$EC2_HOST" bash <<EOF
  set -e
  if [ -d "$REMOTE_DIR/.git" ]; then
    echo "  Pulling latest changes…"
    cd "$REMOTE_DIR"
    git pull --ff-only
  else
    echo "  Cloning repository…"
    sudo mkdir -p "$REMOTE_DIR"
    sudo chown ubuntu:ubuntu "$REMOTE_DIR"
    git clone "$REPO_URL" "$REMOTE_DIR"
    cd "$REMOTE_DIR"
  fi

  echo "  Building and starting containers…"
  docker compose up -d --build

  echo "  Waiting for health check…"
  sleep 5
  curl -sf http://localhost:8000/health && echo "  ✓ Health check passed"
EOF

echo "✓ Deployment complete. Dashboard: http://$EC2_HOST:8000/dashboard"
