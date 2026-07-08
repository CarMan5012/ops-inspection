"""
dingtalk_builder.py
钉钉 Markdown 消息内容构建器

统一管理巡检任务通知和周期报告通知的消息格式，
严格遵循以下排版规范：
  - 标题：### 三级标题 + font 标签蓝色
  - 成功文本：<font color="#00a854">绿色</font>
  - 失败文本：<font color="#f04134">红色</font>
  - 中立信息：<font color="#1890FF">蓝色</font>
  - emoji 开关：enable_emoji=True 时加表情，否则全部剔除
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

# ──────────────────────────────────────────────
# 颜色常量（与设计规范严格对齐）
# ──────────────────────────────────────────────
_C_SUCCESS = "#00a854"   # 成功 - 绿色
_C_FAILED  = "#f04134"   # 失败 - 红色
_C_INFO    = "#1890FF"   # 中立信息 - 蓝色
_C_WARN    = "#fa8c16"   # 警告 - 橙色
_C_MUTED   = "#9e9e9e"   # 静默 - 灰色


def _c(text: str, color: str) -> str:
    """包裹 font 标签颜色。"""
    return f'<font color="{color}">{text}</font>'


def _bold(text: str) -> str:
    return f"**{text}**"


# ──────────────────────────────────────────────
# 公共：构建"巡检任务执行报告"消息
# ──────────────────────────────────────────────
def build_inspection_report_message(
    *,
    job_name: str,
    environment: str,
    status: str,
    success_count: int,
    failed_count: int,
    has_word_report: bool,
    error_summary: str = "",
    mail_status_html: str = "",
    enable_emoji: bool = False,
    notify_time: str | None = None,
) -> tuple[str, str]:
    """
    构建单次巡检任务执行报告的钉钉 Markdown 通知内容。

    Parameters
    ----------
    job_name        : 巡检任务名称
    environment     : 运行环境（如"生产环境"）
    status          : 执行状态，"success" / "failed" / 其他
    success_count   : 成功截图数量
    failed_count    : 失败截图数量
    has_word_report : Word 报告是否生成成功
    error_summary   : 异常摘要（失败时填写）
    mail_status_html: 邮件状态 HTML 片段（可空）
    enable_emoji    : 是否开启 Emoji 表情（全局配置）
    notify_time     : 通知时间字符串（默认当前时间）

    Returns
    -------
    (title, text) : 钉钉消息的标题与正文
    """
    is_success = status == "success"
    now_str = notify_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Emoji 符号（根据开关决定是否留空）
    if enable_emoji:
        title_prefix  = "🟢 " if is_success else "🔴 "
        hint_prefix   = "💡 " if is_success else "⚠️ "
        warn_prefix   = "⚠️ "
    else:
        title_prefix  = ""
        hint_prefix   = ""
        warn_prefix   = ""

    # ── 标题（三级标题，蓝色字体）
    title_text = f"{title_prefix}自动化巡检任务执行报告"
    title      = f"巡检任务执行结果 - {job_name}"

    # ── 执行状态 HTML
    if is_success:
        status_html = _c(_bold("执行成功"), _C_SUCCESS)
    elif status == "failed":
        status_html = _c(_bold("执行失败"), _C_FAILED)
    else:
        status_html = _c(_bold(status), _C_INFO)

    # ── Word 报告状态
    if is_success:
        if has_word_report:
            docx_html = _c("生成成功", _C_SUCCESS)
        else:
            docx_html = _c("生成失败", _C_FAILED)
    else:
        docx_html = _c("未生成", _C_FAILED)

    # ── 截图数量
    sc_success_html = _c(str(success_count), _C_SUCCESS)
    sc_failed_html  = _c(str(failed_count), _C_FAILED if failed_count > 0 else _C_MUTED)
    screenshot_line = (
        f"成功 {sc_success_html} 张 / 失败 {sc_failed_html} 张"
    )

    # ── 通知时间
    time_html = _c(f"`{now_str}`", _C_INFO)

    # ── 正文行组装
    md_lines: list[str] = [
        f"### {_c(title_text, _C_INFO)}",
        "---",
        f"- {_bold('任务名称')}: `{job_name}`",
        f"- {_bold('运行环境')}: {_c(f'`{environment}`', _C_INFO)}",
        f"- {_bold('执行状态')}: {status_html}",
        f"- {_bold('Word 报告')}: {docx_html}",
        f"- {_bold('截图数量')}: {screenshot_line}",
    ]

    if mail_status_html:
        md_lines.append(f"- {_bold('邮件状态')}: {mail_status_html}")

    md_lines.append(f"- {_bold('通知时间')}: {time_html}")
    md_lines.append("---")

    # ── 底部提示
    # 单次任务通知不提示下载（用户通常在月底或周一集中下载，由周报/月报通知引导）
    if is_success:
        md_lines.append(
            f"{hint_prefix}*巡检执行完成，详细报告请登录 Web 后台查看。*"
        )
    else:
        md_lines.append(
            f"{hint_prefix}*警告：巡检任务出现异常，请及时排查服务器状态！*"
        )


    # ── 异常摘要（仅在有内容时追加）
    if error_summary:
        clean_err = error_summary.strip()
        if len(clean_err) > 300:
            clean_err = clean_err[:300] + "..."
        md_lines.append(
            f"\n{warn_prefix}{_bold('异常摘要')}:\n```\n{clean_err}\n```"
        )

    text = "\n".join(md_lines)
    return title, text


# ──────────────────────────────────────────────
# 公共：构建"周期汇总报告"消息
# ──────────────────────────────────────────────
def build_periodic_report_message(
    *,
    setting_name: str,
    report_type: str,
    period_start: str,
    period_end: str,
    status: str,
    run_count: int,
    include_docx: bool,
    zip_size_str: str = "-",
    error_summary: str = "",
    unfinished_reasons: list[str] | None = None,
    mail_status_html: str = "",
    enable_emoji: bool = False,
    notify_time: str | None = None,
) -> tuple[str, str]:
    """
    构建周期汇总报告（周报/月报）的钉钉 Markdown 通知内容。

    Returns
    -------
    (title, text) : 钉钉消息的标题与正文
    """
    is_success     = status in ("success", "partial_success")
    is_partial     = status == "partial_success"
    report_type_cn = "周报" if report_type == "weekly" else "月报"
    now_str        = notify_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Emoji 符号
    if enable_emoji:
        title_prefix  = "🟢 " if is_success else "🔴 "
        hint_prefix   = "💡 " if is_success else "⚠️ "
        warn_prefix   = "⚠️ "
        cross_prefix  = "❌ "
    else:
        title_prefix  = ""
        hint_prefix   = ""
        warn_prefix   = ""
        cross_prefix  = ""

    # ── 标题
    title_text = f"{title_prefix}周期汇总报告通知（{report_type_cn}）"
    title      = f"周期汇总报告生成通知 - {setting_name}"

    # ── 执行状态 HTML
    if status == "success":
        status_html = _c(_bold("生成并归档成功"), _C_SUCCESS)
    elif status == "partial_success":
        status_html = _c(_bold("部分成功（超时强发）"), _C_WARN)
    elif status == "failed":
        status_html = _c(_bold("生成失败"), _C_FAILED)
    else:
        status_html = _c(_bold(status), _C_INFO)

    # ── Word 报告状态
    docx_html = (
        _c("已打入归档包", _C_SUCCESS) if include_docx
        else _c("仅打包截图", _C_MUTED)
    )

    # ── 时间
    time_html = _c(f"`{now_str}`", _C_INFO)

    # ── 正文行
    md_lines: list[str] = [
        f"### {_c(title_text, _C_INFO)}",
        "---",
        f"- {_bold('配置名称')}: `{setting_name}`",
        f"- {_bold('报告类型')}: {_c(report_type_cn, _C_INFO)}",
        f"- {_bold('统计周期')}: `{period_start[:10]}` 至 `{period_end[:10]}`",
        f"- {_bold('执行状态')}: {status_html}",
        f"- {_bold('Word 报告')}: {docx_html}（共 {_c(str(run_count), _C_INFO)} 个）",
        f"- {_bold('归档大小')}: `{zip_size_str}`",
    ]

    if mail_status_html:
        md_lines.append(f"- {_bold('邮件状态')}: {mail_status_html}")

    md_lines.append(f"- {_bold('通知时间')}: {time_html}")
    md_lines.append("---")

    # ── 底部提示
    if status == "failed":
        md_lines.append(
            f"{hint_prefix}*警告：巡检任务出现异常，请及时排查服务器状态！*"
        )
        if error_summary:
            clean_err = error_summary.strip()[:300]
            md_lines.append(
                f"\n{cross_prefix}{_bold('失败原因')}: {clean_err}"
            )
    else:
        md_lines.append(
            f"{hint_prefix}*提示：报告归档包已生成，请登录 Web 后台下载。*"
        )

    # ── 超时未完成任务列表
    if is_partial and unfinished_reasons:
        reasons_text = "\n- ".join(unfinished_reasons)
        md_lines.append(
            f"\n{warn_prefix}{_bold('超时未完成的任务')}:\n- {reasons_text}"
        )

    text = "\n".join(md_lines)
    return title, text


# ──────────────────────────────────────────────
# 工具：构建邮件状态的 HTML 片段（供 runner/periodic 复用）
# ──────────────────────────────────────────────
def build_mail_status_html(mail_status: str) -> str:
    """
    将邮件发送状态字符串转换为带颜色的 HTML 片段。
    返回空字符串表示不需要展示。
    """
    if not mail_status or "skipped" in mail_status.lower():
        return ""
    s = mail_status.lower()
    if "sent" in s or "success" in s:
        return _c("发送成功", _C_SUCCESS)
    if "failed" in s or "error" in s:
        return _c(f"发送失败 ({mail_status})", _C_FAILED)
    return _c(mail_status, _C_MUTED)
