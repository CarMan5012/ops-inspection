from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger("app.dingtalk")


def send_dingtalk_msg(
    webhook: str,
    secret: str | None,
    keyword: str | None,
    title: str,
    text: str,
) -> str:
    """
    通过钉钉机器人 Webhook 发送 Markdown 消息。
    - webhook: 钉钉群机器人的 webhook url
    - secret: 加签安全密钥（可选）
    - keyword: 自定义安全关键词（可选）
    - title: 首屏会话透出的标题
    - text: markdown 格式的正文内容
    返回: 发送状态字符串，如 "sent" 或 "failed: ..."
    """
    if not webhook:
        return "failed: webhook is empty"

    webhook = webhook.strip()
    secret = (secret or "").strip()
    keyword = (keyword or "").strip()

    # 1. 确保安全关键词包含在内容中以防安全拦截
    if keyword:
        prefix = f"[{keyword}] "
        if not title.startswith(prefix) and not title.startswith(keyword):
            title = prefix + title
        if not text.startswith(prefix) and not text.startswith(keyword):
            text = prefix + text

    # 2. 如果配置了加签，计算 timestamp 和 sign
    url = webhook
    if secret:
        try:
            timestamp = str(round(time.time() * 1000))
            secret_enc = secret.encode("utf-8")
            string_to_sign = f"{timestamp}\n{secret}"
            string_to_sign_enc = string_to_sign.encode("utf-8")
            hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
            
            connector = "&" if "?" in webhook else "?"
            url = f"{webhook}{connector}timestamp={timestamp}&sign={sign}"
        except Exception as e:
            logger.error(f"钉钉签名计算失败: {e}", exc_info=True)
            return f"failed: sign_error: {e}"

    # 3. 发送 HTTP POST 请求
    try:
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": text,
            },
        }
        
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=15) as response:
            res_body = response.read().decode("utf-8")
            res_json = json.loads(res_body)
            errcode = res_json.get("errcode")
            errmsg = res_json.get("errmsg")
            
            if errcode == 0:
                logger.info("成功发送钉钉 Webhook 消息。")
                return "sent"
            else:
                logger.error(f"钉钉 Webhook 返回错误 - errcode: {errcode}, errmsg: {errmsg}")
                return f"failed: errcode={errcode}, errmsg={errmsg}"
                
    except Exception as e:
        logger.error(f"请求钉钉 Webhook 时发生异常: {e}", exc_info=True)
        return f"failed: {type(e).__name__}: {e}"
