docker run -d \
  --name test \
  --restart always \
  -p 8000:8000 \
  --security-opt seccomp=unconfined \
  -e TZ=Asia/Shanghai \
  -e APP_NAME="Report" \
  -e APP_SECRET_KEY="test" \
  -e ADMIN_USERNAME="admin" \
  -e ADMIN_PASSWORD="admin" \
  ops-inspection:latest


docker run -d `
  --name test `
  --restart always `
  -p 8100:8000 `
  -e TZ=Asia/Shanghai `
  -e APP_NAME="Report" `
  -e APP_SECRET_KEY="test" `
  -e ADMIN_USERNAME="admin" `
  -e ADMIN_PASSWORD="admin" `
  ops-inspection:latest
