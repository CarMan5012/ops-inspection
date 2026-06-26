# Docker Secret

生产环境创建主密钥文件：

```bash
mkdir -p secrets
python -c "import secrets; print(secrets.token_urlsafe(48))" > secrets/app_secret_key.txt
chmod 600 secrets/app_secret_key.txt
```

`app_secret_key.txt` 是数据库敏感信息的主密钥，必须单独备份，不能提交到 Git。
