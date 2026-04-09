#!/bin/bash
set -e

OWNER="$1"
REPO="$2"
HASH="$3"
S3_BUCKET="$4"
S3_KEY="$5"

APP_DIR="/opt/deployments/apps/${OWNER}/${REPO}"
HASH_FILE="${APP_DIR}/.deploy_hash"
MANIFEST_FILE="${APP_DIR}/deploy_manifest.json"

# Check duplicate deploy
if [ -f "$HASH_FILE" ] && [ "$(cat "$HASH_FILE")" = "$HASH" ]; then
  echo "SKIP: Artifact hash unchanged"
  exit 0
fi

# Prepare directory
mkdir -p "$APP_DIR"

# Download artifact from S3
aws s3 cp "s3://${S3_BUCKET}/${S3_KEY}/deploy.tar.gz" "/tmp/deploy_${OWNER}_${REPO}.tar.gz"

# Extract
rm -rf "${APP_DIR}/source"
mkdir -p "${APP_DIR}/source"
tar xzf "/tmp/deploy_${OWNER}_${REPO}.tar.gz" -C "${APP_DIR}/source"
rm -f "/tmp/deploy_${OWNER}_${REPO}.tar.gz"

# Install Python dependencies
cd "${APP_DIR}/source"
python3 -m pip install -r requirements.txt --quiet 2>/dev/null || true

# Assign port (base 9000 + offset)
PORT_REGISTRY="/opt/deployments/.port_registry"
touch "$PORT_REGISTRY"
EXISTING_PORT=$(grep "^${OWNER}/${REPO} " "$PORT_REGISTRY" | awk '{print $2}' || true)

if [ -z "$EXISTING_PORT" ]; then
  MAX_PORT=$(awk '{print $2}' "$PORT_REGISTRY" | sort -n | tail -1 || echo 8999)
  NEW_PORT=$((MAX_PORT + 1))
  [ "$NEW_PORT" -lt 9000 ] && NEW_PORT=9000
  echo "${OWNER}/${REPO} ${NEW_PORT}" >> "$PORT_REGISTRY"
  PORT=$NEW_PORT
else
  PORT=$EXISTING_PORT
fi

# Start/restart with PM2
PM2_HOME=/etc/.pm2
export PM2_HOME
PM2_NAME="${OWNER}--${REPO}"

pm2 delete "$PM2_NAME" 2>/dev/null || true
cd "${APP_DIR}/source"
pm2 start "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}" \
  --name "$PM2_NAME" \
  --interpreter none \
  -- --no-autorestart
pm2 save

# Configure Nginx reverse proxy
NGINX_CONF="/opt/deployments/nginx/${OWNER}__${REPO}.conf"
mkdir -p /opt/deployments/nginx

cat > "$NGINX_CONF" <<NGINX
location /${OWNER}/${REPO}/ {
    proxy_pass http://127.0.0.1:${PORT}/;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
}
NGINX

# Reload Nginx
nginx -t && systemctl reload nginx

# Save deploy hash & manifest
echo "$HASH" > "$HASH_FILE"
cat > "$MANIFEST_FILE" <<MANIFEST
{
  "owner": "${OWNER}",
  "repo": "${REPO}",
  "hash": "${HASH}",
  "port": ${PORT},
  "runtime": "python",
  "deployed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
MANIFEST

# Update dashboard
/opt/deployments/update_dashboard.sh 2>/dev/null || true

echo "SUCCESS: Deployed ${OWNER}/${REPO} on port ${PORT}"
