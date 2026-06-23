from __future__ import annotations

import logging
import mimetypes
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any

# 初始化日志记录器
logger = logging.getLogger("mail_sender")


def parse_addresses(value: str | list[str] | None) -> list[str]:
    """解析并格式化收件人/抄送/密送邮箱地址列表"""
    if not value:
        return []
    if isinstance(value, str):
        # 兼容英文逗号、分号及空格分隔的地址
        return [addr.strip() for addr in value.replace(";", ",").split(",") if addr.strip()]
    if isinstance(value, list):
        return [str(addr).strip() for addr in value if addr and isinstance(addr, (str, Path)) or True]
    return []


def send_email(
    smtp_config: dict[str, Any],
    to: str | list[str],
    subject: str,
    content: str,
    html: bool = False,
    attachments: str | Path | list[str | Path] | None = None,
    cc: str | list[str] | None = None,
    bcc: str | list[str] | None = None,
) -> dict[str, Any]:
    """
    通用邮件发送函数

    参数:
    - smtp_config: SMTP 服务配置字典，要求包含以下字段:
        * smtp_host (str): SMTP 服务器地址
        * smtp_port (int): SMTP 服务端口
        * smtp_username (str): 登录用户名
        * smtp_password (str): 登录密码或授权码
        * sender_email (str): 发件人邮箱地址
        * use_ssl (bool): 是否使用 SSL 模式连接 (465端口推荐)
        * use_tls (bool): 是否使用 STARTTLS 模式连接 (587/25端口推荐)
    - to: 主收件人地址，支持单个邮箱字符串或邮箱列表
    - subject: 邮件主题
    - content: 邮件正文内容
    - html: 是否作为 HTML 格式发送，默认为 False (纯文本)
    - attachments: 附件文件路径，支持单个路径或路径列表
    - cc: 抄送人邮箱列表
    - bcc: 密送人邮箱列表

    返回:
    - dict: {"success": bool, "message": str, "error_detail": str}
    """
    # 1. 校验 SMTP 配置完整性
    required_fields = ["smtp_host", "smtp_port", "smtp_username", "smtp_password", "sender_email"]
    for field in required_fields:
        if not smtp_config or not smtp_config.get(field):
            err_msg = f"邮件配置不完整：字段 '{field}' 不能为空"
            logger.error(err_msg)
            return {"success": False, "message": err_msg, "error_detail": "Missing Required Configuration"}

    # 屏蔽敏感信息用于安全日志记录
    safe_config = {k: (v if k != "smtp_password" else "******") for k, v in smtp_config.items()}
    logger.info(f"开始构建测试邮件. SMTP配置: {safe_config}")

    # 2. 解析收信人地址列表
    to_list = parse_addresses(to)
    cc_list = parse_addresses(cc)
    bcc_list = parse_addresses(bcc)
    
    if not to_list and not cc_list and not bcc_list:
        err_msg = "收信人列表为空（To, Cc, Bcc 均无有效邮箱地址）"
        logger.error(err_msg)
        return {"success": False, "message": err_msg, "error_detail": "No Recipients"}

    # 3. 创建 EmailMessage 邮件对象
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_config["sender_email"]
    message["To"] = ", ".join(to_list)
    
    if cc_list:
        message["Cc"] = ", ".join(cc_list)
    if bcc_list:
        # Bcc 头部仅赋给 EmailMessage 供追踪，发送前很多邮件服务器会剥离它
        message["Bcc"] = ", ".join(bcc_list)

    # 4. 设置正文内容
    if html:
        message.set_content(content, subtype="html")
    else:
        message.set_content(content)

    # 5. 添加附件
    if attachments:
        if isinstance(attachments, (str, Path)):
            attachments = [attachments]
        
        for file_path in attachments:
            path = Path(file_path)
            if not path.exists() or not path.is_file():
                err_msg = f"附件文件未找到或不是合法文件: {file_path}"
                logger.error(err_msg)
                return {"success": False, "message": err_msg, "error_detail": "Attachment Not Found"}
            
            # 自动探测文件 MIME 类型
            mime_type, encoding = mimetypes.guess_type(str(path))
            if mime_type is None or encoding is not None:
                # 无法识别时降级为通用二进制流
                mime_type = "application/octet-stream"
            
            main_type, sub_type = mime_type.split("/", 1)
            try:
                with path.open("rb") as f:
                    message.add_attachment(
                        f.read(),
                        maintype=main_type,
                        subtype=sub_type,
                        filename=path.name
                    )
                logger.info(f"成功挂载附件: {path.name}")
            except Exception as e:
                err_msg = f"读取或挂载附件 '{path.name}' 失败: {e}"
                logger.error(err_msg, exc_info=True)
                return {"success": False, "message": err_msg, "error_detail": str(e)}

    # 6. 开始 SMTP 连接与发送
    host = str(smtp_config["smtp_host"])
    port = int(smtp_config["smtp_port"])
    username = str(smtp_config["smtp_username"])
    password = str(smtp_config["smtp_password"])
    use_ssl = bool(smtp_config.get("use_ssl", False))
    use_tls = bool(smtp_config.get("use_tls", False))

    # 合并所有实际投递的目标地址列表 (To + Cc + Bcc)
    all_recipients = to_list + cc_list + bcc_list

    try:
        if use_ssl:
            logger.info(f"正在以 SSL 模式建立连接 [{host}:{port}]...")
            with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
                logger.info("SSL 连接建立成功，开始验证发信账户...")
                smtp.login(username, password)
                logger.info("账户登录成功，正在投递邮件数据...")
                smtp.send_message(message, to_addrs=all_recipients)
        else:
            logger.info(f"正在以标准模式建立连接 [{host}:{port}]...")
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                if use_tls:
                    logger.info("正在执行 STARTTLS 安全握手...")
                    smtp.starttls()
                logger.info("连接就绪，开始验证发信账户...")
                smtp.login(username, password)
                logger.info("账户登录成功，正在投递邮件数据...")
                smtp.send_message(message, to_addrs=all_recipients)
                
        success_msg = "邮件发送成功"
        logger.info(success_msg)
        return {"success": True, "message": success_msg, "error_detail": ""}

    except smtplib.SMTPAuthenticationError as e:
        err_msg = "发信邮箱账号验证失败，请确认您的用户名或授权密码是否填写正确"
        logger.error(f"{err_msg}: {e}")
        return {"success": False, "message": err_msg, "error_detail": str(e)}
        
    except smtplib.SMTPConnectError as e:
        err_msg = "无法连接至邮件服务器，请检查 SMTP 服务器主机名、端口号或防火墙限制"
        logger.error(f"{err_msg}: {e}")
        return {"success": False, "message": err_msg, "error_detail": str(e)}
        
    except smtplib.SMTPDataError as e:
        err_msg = "邮件发送被服务器拒绝，可能被反垃圾防邮件安全网关拦截，请调整正文或核对发件人一致性"
        logger.error(f"{err_msg}: {e}")
        return {"success": False, "message": err_msg, "error_detail": str(e)}
        
    except Exception as e:
        err_msg = f"发送邮件发生未知网络或协议异常: {type(e).__name__}"
        logger.error(f"{err_msg}: {e}", exc_info=True)
        return {"success": False, "message": f"发送失败: {e}", "error_detail": str(e)}
