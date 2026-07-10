#!/bin/bash

if [ "$(docker ps -aq -f name=ops-inspection)" ]; then
    echo "正在停止并删除旧的 ops-inspection 容器..."
    docker stop ops-inspection || true
    docker rm -f ops-inspection || true
fi

echo "正在启动新版本 ops-inspection 容器..."
docker run -d \
  --name ops-inspection \
  --restart always \
  -p 8000:8000 \
  --security-opt seccomp=unconfined \
  -e TZ=Asia/Shanghai \
  -e APP_NAME="运维管理后台" \
  -e APP_DATA_DIR=/app/data \
  -e SQLCIPHER_DB_KEY="u4R9eKd7Tz2WgS3jXn8vM4bQ1yP6hL5vD7kC9sR2aT8" \
  -e ADMIN_USERNAME=admin \
  -e ADMIN_PASSWORD="Eanb#BZx2lusQVvn" \
  -v /data/ops-inspection/data:/app/data \
  -v /data/ops-inspection/secrets:/run/secrets \
  -e APP_SECRET_KEY_FILE=/run/secrets/app_secret_key \
  ops-inspection:v1.0.1

# 限制数据目录权限（只允许所有者读写执行）
chmod 700 /data/ops-inspection/data
# 限制密钥目录及文件的权限（只允许所有者读写）
chmod 700 /data/ops-inspection/secrets
chmod 600 /data/ops-inspection/secrets/app_secret_key


