#!/bin/bash
# One-shot: configure api.pwd.kr nginx virtual host on EC2 (SSL)
set -e

DOMAIN_CONF="/etc/nginx/conf.d/api.pwd.kr.conf"

cat > "$DOMAIN_CONF" <<'DOMAINEOF'
server {
    listen 443 ssl;
    server_name api.pwd.kr;

    ssl_certificate /etc/nginx/ssl/api.pwd.kr.crt;
    ssl_certificate_key /etc/nginx/ssl/api.pwd.kr.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    include /opt/deployments/nginx/*.conf;

    location / {
        root /opt/deployments/www;
        index index.html;
        try_files $uri $uri/ =404;
    }
}

server {
    listen 80;
    server_name api.pwd.kr;
    return 301 https://$host$request_uri;
}
DOMAINEOF

nginx -t && systemctl reload nginx
echo "SUCCESS: api.pwd.kr virtual host configured (SSL)"
