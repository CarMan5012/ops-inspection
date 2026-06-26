docker run -d \
  --name ops-inspection \
  --restart always \
  -p 8000:8000 \
  --security-opt seccomp=unconfined \
  -e TZ=Asia/Shanghai \
  -e APP_NAME="运维管理后台" \
  -e APP_DATA_DIR=/app/data \
  -e APP_SECRET_KEY_FILE=/run/secrets/app_secret_key \
  -e ADMIN_USERNAME=admin \
  -e ADMIN_PASSWORD="Eanb#BZx2lusQVvn" \
  -v /data/ops-inspection/data:/app/data \
  -v /data/ops-inspection/data/secrets:/run/secrets \
  ops-inspection:latest




https://oapi.dingtalk.com/robot/send?access_token=979f17a5364bc75b46ad85dc64f35b19f3fbbcdd665d630a999c6cc2f2b612e6


admin adminhsh-2025cX6
elastic cMYGC7Z35t4FPa2n
c04073528454de450700