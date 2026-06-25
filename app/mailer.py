from __future__ import annotations

import os
import smtplib
import time
import socket
import logging
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from app.utils import parse_csv, render_template, today_text, resolve_mail_credential

logger = logging.getLogger("app.mailer")


def _execute_send_mail(mail_profile: dict[str, Any], message: EmailMessage, password: str, all_recipients: list[str]) -> None:
    host = str(mail_profile["smtp_host"])
    port = int(mail_profile.get("smtp_port") or 465)
    username = str(mail_profile.get("username") or "")
    use_ssl = int(mail_profile.get("use_ssl") or 0)
    use_starttls = int(mail_profile.get("use_starttls") or 0)

    max_retries = 3
    retry_delay = 5  # seconds

    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                logger.info(f"正在尝试重新发送邮件 (第 {attempt} 次重试)...")
            
            if use_ssl:
                with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
                    if username:
                        smtp.login(username, password)
                    smtp.send_message(message, to_addrs=all_recipients)
            else:
                with smtplib.SMTP(host, port, timeout=30) as smtp:
                    if use_starttls:
                        smtp.starttls()
                    if username:
                        smtp.login(username, password)
                    smtp.send_message(message, to_addrs=all_recipients)
            
            return
            
        except (smtplib.SMTPDataError, smtplib.SMTPConnectError, smtplib.SMTPHeloError, 
                socket.timeout, TimeoutError, ConnectionError) as exc:
            is_transient = True
            if isinstance(exc, smtplib.SMTPDataError):
                if 500 <= exc.smtp_code < 600:
                    is_transient = False
            
            if not is_transient:
                logger.error(f"遭遇永久性 SMTP 错误 (code {exc.smtp_code})，放弃发送。错误详情: {exc}")
                raise exc
                
            if attempt < max_retries:
                logger.warning(
                    f"发送邮件遭遇临时错误 (尝试 {attempt + 1}/{max_retries + 1}): "
                    f"{type(exc).__name__}: {exc}。将在 {retry_delay} 秒后重试。"
                )
                time.sleep(retry_delay)
            else:
                logger.error(f"邮件发送最终失败，已达最大重试次数: {type(exc).__name__}: {exc}")
                raise exc
        except Exception as exc:
            logger.error(f"遭遇无法重试的非预期发信错误: {type(exc).__name__}: {exc}")
            raise exc



def send_report_mail(mail_profile: dict[str, Any], job: dict[str, Any], report_path: str | None, error_msg: str = "") -> str:
    if not mail_profile:
        return "skipped: no mail profile"
    if not mail_profile.get("smtp_host"):
        return "skipped: smtp host empty"
    recipients = parse_csv(str(mail_profile.get("recipients") or ""))
    cc = parse_csv(str(mail_profile.get("cc") or ""))
    if not recipients and not cc:
        return "skipped: recipients empty"

    password = resolve_mail_credential(mail_profile)
    context = {
        "date": today_text(),
        "job": str(job.get("name") or ""),
        "environment": str(job.get("environment") or ""),
        "time_range": str(job.get("time_range_label") or ""),
    }

    message = EmailMessage()
    message["Subject"] = render_template(str(mail_profile.get("subject_template") or ""), context)
    message["From"] = str(mail_profile.get("sender") or mail_profile.get("username") or "")
    message["To"] = ", ".join(recipients)
    if cc:
        message["Cc"] = ", ".join(cc)
        
    body_text = render_template(str(mail_profile.get("body_template") or ""), context)
    if error_msg:
        body_text += f"\n\n[系统提示] 该巡检任务执行期间发生异常，未生成巡检报告。错误详情如下：\n{error_msg}"
    message.set_content(body_text)

    if report_path:
        path = Path(report_path)
        if path.exists() and path.is_file():
            with path.open("rb") as file_obj:
                message.add_attachment(
                    file_obj.read(),
                    maintype="application",
                    subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
                    filename=path.name,
                )

    host = str(mail_profile["smtp_host"])
    port = int(mail_profile.get("smtp_port") or 465)
    username = str(mail_profile.get("username") or "")
    all_recipients = recipients + cc
    _execute_send_mail(mail_profile, message, password, all_recipients)
    return "sent"


def send_periodic_report_mail(
    mail_profile: dict[str, Any],
    subject: str,
    body: str,
    attachment_path: str | None = None,
    recipients_override: str | None = None
) -> str:
    if not mail_profile:
        return "skipped: no mail profile"
    if not mail_profile.get("smtp_host"):
        return "skipped: smtp host empty"
        
    dest_recipients = recipients_override if recipients_override else mail_profile.get("recipients")
    recipients = parse_csv(str(dest_recipients or ""))
    cc = parse_csv(str(mail_profile.get("cc") or "")) if not recipients_override else []
    
    if not recipients:
        return "skipped: recipients empty"

    password = resolve_mail_credential(mail_profile)

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = str(mail_profile.get("sender") or mail_profile.get("username") or "")
    message["To"] = ", ".join(recipients)
    if cc:
        message["Cc"] = ", ".join(cc)
    message.set_content(body)

    if attachment_path:
        path = Path(attachment_path)
        if path.exists():
            with path.open("rb") as file_obj:
                message.add_attachment(
                    file_obj.read(),
                    maintype="application",
                    subtype="zip",
                    filename=path.name,
                )

    host = str(mail_profile["smtp_host"])
    port = int(mail_profile.get("smtp_port") or 465)
    username = str(mail_profile.get("username") or "")
    all_recipients = recipients + cc
    _execute_send_mail(mail_profile, message, password, all_recipients)
    return "sent"

