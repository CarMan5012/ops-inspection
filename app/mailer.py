from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from app.utils import parse_csv, render_template, today_text, resolve_mail_credential


def send_report_mail(mail_profile: dict[str, Any], job: dict[str, Any], report_path: str) -> str:
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
    message.set_content(render_template(str(mail_profile.get("body_template") or ""), context))

    path = Path(report_path)
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

    if int(mail_profile.get("use_ssl") or 0):
        with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
            if username:
                smtp.login(username, password)
            smtp.send_message(message, to_addrs=all_recipients)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            if int(mail_profile.get("use_starttls") or 0):
                smtp.starttls()
            if username:
                smtp.login(username, password)
            smtp.send_message(message, to_addrs=all_recipients)

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

    if int(mail_profile.get("use_ssl") or 0):
        with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
            if username:
                smtp.login(username, password)
            smtp.send_message(message, to_addrs=all_recipients)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            if int(mail_profile.get("use_starttls") or 0):
                smtp.starttls()
            if username:
                smtp.login(username, password)
            smtp.send_message(message, to_addrs=all_recipients)

    return "sent"

