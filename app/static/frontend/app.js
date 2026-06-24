const state = {
  view: "dashboard",
  jobs: [],
  runs: [],
  authProfiles: [],
  mailProfiles: [],
  periodic: { settings: [], runs: [] },
  metrics: {},
};

let config = {
  apiBase: "/api",
  frontendBase: "/",
  artifactBase: "/artifact"
};

function apiUrl(path) {
  const prefix = config.apiBase === "/" ? "" : config.apiBase;
  const suffix = path.startsWith("/") ? path : "/" + path;
  return prefix + suffix;
}

function getFrontendUrl(path) {
  const prefix = config.frontendBase === "/" ? "" : config.frontendBase;
  const suffix = path.startsWith("/") ? path : "/" + path;
  return prefix + suffix;
}

const route = {
  jobDetail: (id) => getFrontendUrl(`jobs/${id}`),
  runDetail: (id) => getFrontendUrl(`runs/${id}`),
  periodicEdit: (type) => getFrontendUrl(`periodic-reports/${type}/edit`),
};

const viewToHash = {
  "dashboard": "#/jobs",
  "runs": "#/runs",
  "periodic": "#/periodic",
  "auth": "#/auth",
  "mail": "#/mail"
};

function handleRoute() {
  const hash = location.hash || "";
  let view = "dashboard";
  if (hash === "" || hash === "#/" || hash === "#/jobs") {
    view = "dashboard";
  } else if (hash === "#/runs") {
    view = "runs";
  } else if (hash === "#/periodic") {
    view = "periodic";
  } else if (hash === "#/auth") {
    view = "auth";
  } else if (hash === "#/mail") {
    view = "mail";
  }
  setView(view);
}

const regions = {
  metrics: document.querySelector('[data-region="metrics"]'),
  jobs: document.querySelector('[data-region="jobs"]'),
  recentRuns: document.querySelector('[data-region="recent-runs"]'),
  runs: document.querySelector('[data-region="runs"]'),
  auth: document.querySelector('[data-region="auth-profiles"]'),
  mail: document.querySelector('[data-region="mail-profiles"]'),
  periodic: document.querySelector('[data-region="periodic"]'),
  toasts: document.querySelector('[data-region="toasts"]'),
  modal: document.querySelector('[data-region="modal"]'),
};

const authSelectorDefaults = {
  username: 'input[name="user"], input[name="username"], input[type="email"], input[placeholder*="username"], input[placeholder*="email"], input[placeholder*="user"]',
  password: 'input[name="password"], input[type="password"], input[placeholder*="password"]',
  submit: 'button[type="submit"], button:has-text("Log in"), button:has-text("Login"), button:has-text("登录"), input[type="submit"]',
};

const authSuccessDefaults = {
  grafana: ".react-grid-layout",
  kibana: "[data-test-subj='dashboardViewport']",
  custom: "",
  none: "",
};

document.addEventListener("DOMContentLoaded", async () => {
  try {
    const res = await fetch("/app-config.json");
    if (res.ok) {
      const serverConfig = await res.json();
      config.apiBase = serverConfig.apiBase || config.apiBase;
      config.frontendBase = serverConfig.frontendBase || config.frontendBase;
      config.artifactBase = serverConfig.artifactBase || config.artifactBase;
    }
  } catch (e) {
    console.error("加载全局配置失败，使用默认配置:", e);
  }
  bindStaticActions(document);
  window.addEventListener("hashchange", handleRoute);
  handleRoute();
  loadAll();

  // 捕获 URL 参数中的消息并以 Toast 提示显示
  const params = new URLSearchParams(window.location.search);
  const successMsg = params.get("success");
  const errorMsg = params.get("error");
  if (successMsg) {
    showSuccess(successMsg);
  }
  if (errorMsg) {
    showError(errorMsg);
  }
  if (successMsg || errorMsg) {
    const newUrl = window.location.pathname + window.location.hash;
    window.history.replaceState({}, document.title, newUrl);
  }
});

function bindStaticActions(root) {
  root.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => {
      const view = button.dataset.view;
      location.hash = viewToHash[view] || "";
    });
  });

  root.querySelectorAll("[data-view-shortcut]").forEach((button) => {
    button.addEventListener("click", () => {
      const view = button.dataset.viewShortcut;
      location.hash = viewToHash[view] || "";
    });
  });

  root.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", (event) => {
      handleAction(button.dataset.action, button.dataset.id, button);
      event.stopPropagation();
    });
  });
}

function bindDynamicActions(root) {
  root.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", (event) => {
      handleAction(button.dataset.action, button.dataset.id, button);
      event.stopPropagation();
    });
  });
}

function handleAction(action, id, triggerButton = null) {
  if (action === "refresh") {
    loadAll(true);
  } else if (action === "refresh-periodic") {
    loadPeriodic(true);
  } else if (action === "new-job") {
    openJobModal();
  } else if (action === "edit-job") {
    openJobModal(state.jobs.find((job) => Number(job.id) === Number(id)));
  } else if (action === "delete-job") {
    deleteResource(`/jobs/${id}`, "任务已删除");
  } else if (action === "run-job") {
    runJob(id, triggerButton);
  } else if (action === "new-auth") {
    openAuthModal();
  } else if (action === "edit-auth") {
    openAuthModal(state.authProfiles.find((profile) => Number(profile.id) === Number(id)));
  } else if (action === "test-auth") {
    testAuth(id, triggerButton);
  } else if (action === "delete-auth") {
    deleteResource(`/auth-profiles/${id}`, "认证配置已删除");
  } else if (action === "new-mail") {
    openMailModal();
  } else if (action === "edit-mail") {
    openMailModal(state.mailProfiles.find((profile) => Number(profile.id) === Number(id)));
  } else if (action === "delete-mail") {
    deleteResource(`/mail-profiles/${id}`, "邮件配置已删除");
  } else if (action === "run-periodic") {
    runPeriodic(id, triggerButton);
  } else if (action === "close-modal") {
    closeModal();
  }
}

function setView(view) {
  state.view = view;
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  document.querySelectorAll("[data-view-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.viewPanel !== view;
  });
}

async function loadAll(showToast = false) {
  renderLoading();
  try {
    const dashboard = await apiGet("/dashboard");
    state.jobs = dashboard.jobs || [];
    state.runs = dashboard.runs || [];
    state.authProfiles = dashboard.auth_profiles || [];
    state.mailProfiles = dashboard.mail_profiles || [];
    state.metrics = dashboard.metrics || {};

    renderMetrics();
    renderJobs();
    renderRecentRuns();
    renderRuns();
    renderAuthProfiles();
    renderMailProfiles();
    await loadPeriodic(false);

    if (showToast) {
      showSuccess("数据已刷新");
    }
  } catch (error) {
    renderGlobalError(error);
  }
}

async function loadPeriodic(showToast = false) {
  try {
    state.periodic = await apiGet("/periodic-reports");
    renderPeriodic();
    if (showToast) {
      showSuccess("周期报告数据已刷新");
    }
  } catch (error) {
    renderError(regions.periodic, error);
  }
}

function renderLoading() {
  regions.metrics.innerHTML = loadingMetrics();
  regions.jobs.innerHTML = loadingTable(6);
  regions.recentRuns.innerHTML = loadingTable(5);
  regions.runs.innerHTML = loadingTable(8);
  regions.auth.innerHTML = loadingTable(4);
  regions.mail.innerHTML = loadingTable(4);
  regions.periodic.innerHTML = loadingTable(4);
}

function renderMetrics() {
  const items = [
    ["巡检任务总数", state.metrics.jobs_total || 0],
    ["启用中的任务", state.metrics.jobs_enabled || 0],
    ["最近成功运行", state.metrics.runs_success || 0],
    ["最近运行失败", state.metrics.runs_failed || 0],
  ];
  regions.metrics.innerHTML = items.map(([label, value]) => `
    <div>
      <span>${escapeHtml(label)}</span>
      <strong>${Number(value).toLocaleString("zh-CN")} 个</strong>
    </div>
  `).join("");
}

function renderJobs() {
  if (!state.jobs.length) {
    regions.jobs.innerHTML = `<div class="empty">暂无巡检任务。请点击“新增任务”开始配置。</div>`;
    return;
  }

  regions.jobs.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>任务名称</th>
          <th>运行环境</th>
          <th>执行计划</th>
          <th>邮件发送</th>
          <th>计划状态</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        ${state.jobs.map((job) => `
          <tr>
            <td>
              <strong><a href="${route.jobDetail(job.id)}">${escapeHtml(job.name || "未命名任务")}</a></strong>
              ${job.load_error ? `<div class="inline-error">调度异常：${escapeHtml(job.load_error)}</div>` : ""}
            </td>
            <td>${escapeHtml(job.environment || "-")}</td>
            <td><code>${escapeHtml(job.schedule_label || job.cron_expression || "-")}</code></td>
            <td>${asBool(job.send_mail) ? "开启" : "关闭"}</td>
            <td>${asBool(job.enabled) ? '<span class="badge ok">启用</span>' : '<span class="badge">停用</span>'}</td>
            <td>
              <div class="action-row">
                <button type="button" class="button" data-action="run-job" data-id="${job.id}">立即运行</button>
                <button type="button" class="button" data-action="edit-job" data-id="${job.id}">修改配置</button>
                <a class="button" href="${route.jobDetail(job.id)}">详情</a>
              </div>
            </td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
  bindDynamicActions(regions.jobs);
}

function renderRecentRuns() {
  renderRunsTable(regions.recentRuns, state.runs.slice(0, 8));
}

function renderRuns() {
  renderRunsTable(regions.runs, state.runs);
}

function renderRunsTable(region, runs) {
  if (!runs.length) {
    region.innerHTML = `<div class="empty">暂无巡检运行记录。</div>`;
    return;
  }

  region.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>运行 ID</th>
          <th>巡检任务</th>
          <th>执行状态</th>
          <th>开始时间</th>
          <th>成功张数</th>
          <th>失败张数</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        ${runs.map((run) => `
          <tr>
            <td><strong><a href="${route.runDetail(run.id)}">#${run.id}</a></strong></td>
            <td>${escapeHtml(run.job_name || "-")}</td>
            <td>${statusBadge(run.status, run.status_label)}</td>
            <td>${escapeHtml(shortDate(run.started_at))}</td>
            <td><span class="ok-text">${numberText(run.success_count)} 张</span></td>
            <td>${Number(run.failed_count || 0) > 0 ? `<span class="bad-text">${numberText(run.failed_count)} 张</span>` : '<span class="muted-text">0 张</span>'}</td>
            <td>
              <div class="action-row">
                <a class="button" href="${route.runDetail(run.id)}">详情</a>
                ${run.report_url ? `<a class="button primary" href="${escapeAttr(run.report_url)}">下载报告</a>` : ""}
              </div>
            </td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

function renderAuthProfiles() {
  const authTable = state.authProfiles.length ? `
    <table>
      <thead>
        <tr>
          <th>名称</th>
          <th>类型</th>
          <th>登录地址</th>
          <th>凭据</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        ${state.authProfiles.map((profile) => `
          <tr>
            <td><strong>${escapeHtml(profile.name || "未命名认证")}</strong></td>
            <td><code>${escapeHtml(profile.auth_type || "none")}</code></td>
            <td><span class="url-text">${escapeHtml(profile.login_url || "-")}</span></td>
            <td>${profile.has_password ? '<span class="badge ok">已配置</span>' : '<span class="badge">未配置</span>'}</td>
            <td>
              <div class="action-row">
                <button class="button" type="button" data-action="edit-auth" data-id="${profile.id}">编辑</button>
                ${profile.auth_type === "form" ? `<button class="button" type="button" data-action="test-auth" data-id="${profile.id}">测试登录</button>` : ""}
                <button class="button danger" type="button" data-action="delete-auth" data-id="${profile.id}">删除</button>
              </div>
            </td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  ` : `<div class="empty">暂无认证配置。</div>`;
  regions.auth.innerHTML = authTable;
  bindDynamicActions(regions.auth);
}

function renderMailProfiles() {
  const mailTable = state.mailProfiles.length ? `
    <table>
      <thead>
        <tr>
          <th>名称</th>
          <th>SMTP</th>
          <th>发件人</th>
          <th>收件人</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        ${state.mailProfiles.map((profile) => `
          <tr>
            <td><strong>${escapeHtml(profile.name || "未命名邮箱")}</strong></td>
            <td><code>${escapeHtml(profile.smtp_host || "-")}:${escapeHtml(profile.smtp_port || "-")}</code></td>
            <td>${escapeHtml(profile.sender || profile.username || "-")}</td>
            <td>${escapeHtml(profile.recipients || "-")}</td>
            <td>
              <div class="action-row">
                <button class="button" type="button" data-action="edit-mail" data-id="${profile.id}">编辑</button>
                <button class="button danger" type="button" data-action="delete-mail" data-id="${profile.id}">删除</button>
              </div>
            </td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  ` : `<div class="empty">暂无邮件配置。</div>`;
  regions.mail.innerHTML = mailTable;
  bindDynamicActions(regions.mail);
}

function renderPeriodic() {
  const settings = state.periodic.settings || [];
  const runs = state.periodic.runs || [];

  const settingsTable = settings.length ? `
    <table>
      <thead>
        <tr>
          <th>报告类型</th>
          <th>任务名称</th>
          <th>计划</th>
          <th>状态</th>
          <th>内容</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        ${settings.map((item) => `
          <tr>
            <td><code>${escapeHtml(item.report_type || "-")}</code></td>
            <td><strong>${escapeHtml(item.name || "-")}</strong></td>
            <td><code>${escapeHtml(item.schedule_label || item.cron_expression || "-")}</code></td>
            <td>${asBool(item.enabled) ? '<span class="badge ok">启用</span>' : '<span class="badge">停用</span>'}</td>
            <td>${asBool(item.include_docx) ? "Word" : ""}${asBool(item.include_screenshots) ? " 截图" : ""}</td>
            <td>
              <div class="action-row">
                <button class="button" type="button" data-action="run-periodic" data-id="${escapeAttr(item.report_type)}">立即生成</button>
                <a class="button" href="${route.periodicEdit(item.report_type)}">编辑</a>
              </div>
            </td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  ` : `<div class="empty">暂无周期报告配置。</div>`;

  const runsTable = runs.length ? `
    <table>
      <thead>
        <tr>
          <th>运行 ID</th>
          <th>类型</th>
          <th>周期</th>
          <th>状态</th>
          <th>大小</th>
          <th>完成时间</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        ${runs.map((run) => `
          <tr>
            <td>#${run.id}</td>
            <td><code>${escapeHtml(run.report_type || "-")}</code></td>
            <td>${escapeHtml(run.period_start || "-")} - ${escapeHtml(run.period_end || "-")}</td>
            <td>${statusBadge(run.status, run.status)}</td>
            <td>${escapeHtml(run.size_str || "-")}</td>
            <td>${escapeHtml(shortDate(run.finished_at))}</td>
            <td>${run.download_url ? `<a class="button" href="${escapeAttr(run.download_url)}">下载</a>` : "-"}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  ` : `<div class="empty">暂无周期报告包。</div>`;

  regions.periodic.innerHTML = `
    <div class="section-title"><h2>周期报告配置</h2></div>
    ${settingsTable}
    <div class="section-title"><h2>历史报告包</h2></div>
    ${runsTable}
  `;
  bindDynamicActions(regions.periodic);
}

function openJobModal(job = null) {
  const isEdit = Boolean(job);
  openModal(isEdit ? "修改巡检任务" : "新增巡检任务", `
    <form class="form-grid wide" data-form="job">
      ${field("任务名称", "name", job?.name || "", "text", true)}
      ${field("运行环境", "environment", job?.environment || "生产环境")}
      ${field("Cron 表达式", "cron_expression", job?.cron_expression || "0 9 * * *", "text", true)}
      ${field("计划说明", "schedule_label", job?.schedule_label || "")}
      ${field("时间范围", "time_range_label", job?.time_range_label || "最近 24 小时")}
      ${field("报告标题", "report_title", job?.report_title || "自动化巡检报告")}
      ${selectField("邮件配置", "mail_profile_id", job?.mail_profile_id || "", state.mailProfiles.map((profile) => [profile.id, profile.name]))}
      ${field("浏览器宽度", "browser_width", job?.browser_width || 1920, "number")}
      ${field("浏览器高度", "browser_height", job?.browser_height || 1080, "number")}
      ${checkField("启用任务", "enabled", job ? asBool(job.enabled) : true)}
      ${checkField("生成后发邮件", "send_mail", job ? asBool(job.send_mail) : false)}
      ${checkField("无头浏览器", "headless", job ? asBool(job.headless) : true)}
      <div class="form-actions span-2">
        <button class="button" type="button" data-action="close-modal">取消</button>
        <button class="button primary" type="submit">${isEdit ? "保存修改" : "创建任务"}</button>
      </div>
    </form>
  `);

  const form = regions.modal.querySelector('[data-form="job"]');
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitForm(form, isEdit ? `/jobs/${job.id}` : "/jobs", isEdit ? "PUT" : "POST", isEdit ? "任务已更新" : "任务已创建");
  });
}

function openAuthModal(profile = null) {
  const isEdit = Boolean(profile);
  const preset = detectAuthPreset(profile);
  openModal(isEdit ? "修改认证配置" : "新增认证配置", `
    <form class="form-grid wide" data-form="auth">
      ${field("名称", "name", profile?.name || "", "text", true)}
      ${selectField("目标系统", "system_preset", preset, [["grafana", "Grafana 看板"], ["kibana", "Kibana / Elastic"], ["custom", "普通网页"], ["none", "无需登录"]])}
      <input type="hidden" name="auth_type" value="${escapeAttr(profile?.auth_type || "form")}">
      ${field("登录地址", "login_url", profile?.login_url || "", "url", false, true, "例如：http://localhost:3000/login", "login-field")}
      ${field("用户名", "username_value", profile?.username_value || profile?.username || "", "text", false, false, "例如：admin", "login-field")}
      ${field("密码", "password", "", "password", !isEdit, false, isEdit ? "留空则沿用原密码" : "请输入密码", "login-field")}
      <details class="advanced-settings span-2">
        <summary>高级设置</summary>
        <div class="form-grid wide advanced-grid">
          ${field("用户名选择器", "username_selector", profile?.username_selector || authSelectorDefaults.username)}
          ${field("密码选择器", "password_selector", profile?.password_selector || authSelectorDefaults.password)}
          ${field("提交按钮选择器", "submit_selector", profile?.submit_selector || authSelectorDefaults.submit)}
          ${field("成功选择器", "success_selector", profile?.success_selector || "", "text", false, false, "不确定可留空")}
          ${field("浏览器状态文件", "storage_state_path", profile?.storage_state_path || "", "text", false, true, "通常留空，由系统自动管理")}
        </div>
      </details>
      <div class="form-actions span-2">
        <button class="button" type="button" data-action="close-modal">取消</button>
        <button class="button primary" type="submit">${isEdit ? "保存修改" : "创建认证"}</button>
      </div>
    </form>
  `);

  const form = regions.modal.querySelector('[data-form="auth"]');
  bindAuthPreset(form, preset, isEdit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitForm(form, isEdit ? `/auth-profiles/${profile.id}` : "/auth-profiles", isEdit ? "PUT" : "POST", isEdit ? "认证已更新" : "认证已创建");
  });
}

function detectAuthPreset(profile) {
  if (!profile || profile.auth_type === "none") {
    return profile?.auth_type === "none" ? "none" : "grafana";
  }
  const successSelector = String(profile.success_selector || "");
  if (successSelector.includes(".react-grid-layout")) {
    return "grafana";
  }
  if (successSelector.includes("dashboardViewport")) {
    return "kibana";
  }
  return "custom";
}

function bindAuthPreset(form, initialPreset, isEdit = false) {
  const presetSelect = form.querySelector('[name="system_preset"]');
  const authTypeInput = form.querySelector('[name="auth_type"]');
  const loginFields = form.querySelectorAll('[data-auth-group="login-field"]');
  const successSelector = form.querySelector('[name="success_selector"]');
  const usernameSelector = form.querySelector('[name="username_selector"]');
  const passwordSelector = form.querySelector('[name="password_selector"]');
  const submitSelector = form.querySelector('[name="submit_selector"]');

  function apply(forceSuccessSelector = false) {
    const preset = presetSelect.value || initialPreset || "grafana";
    const noLogin = preset === "none";
    authTypeInput.value = noLogin ? "none" : "form";
    loginFields.forEach((label) => {
      label.hidden = noLogin;
      label.querySelectorAll("input").forEach((input) => {
        input.required = !noLogin && (input.name !== "password" || !isEdit);
      });
    });
    usernameSelector.value ||= authSelectorDefaults.username;
    passwordSelector.value ||= authSelectorDefaults.password;
    submitSelector.value ||= authSelectorDefaults.submit;
    if (forceSuccessSelector || !successSelector.value.trim()) {
      successSelector.value = authSuccessDefaults[preset] || "";
    }
  }

  presetSelect.addEventListener("change", () => apply(true));
  apply(false);
}

function openMailModal(profile = null) {
  const isEdit = Boolean(profile);
  openModal(isEdit ? "修改邮件配置" : "新增邮件配置", `
    <form class="form-grid wide" data-form="mail">
      ${field("名称", "name", profile?.name || "", "text", true)}
      ${field("SMTP 主机", "smtp_host", profile?.smtp_host || "", "text", true)}
      ${field("SMTP 端口", "smtp_port", profile?.smtp_port || 465, "number")}
      ${field("用户名", "username", profile?.username || "")}
      ${field("密码", "password", "", "password")}
      ${field("发件人", "sender", profile?.sender || "")}
      ${field("收件人", "recipients", profile?.recipients || "", "text", true)}
      ${field("抄送", "cc", profile?.cc || "")}
      ${field("邮件主题", "subject_template", profile?.subject_template || "自动化巡检报告 - {date}", "text", false, true)}
      ${textareaField("正文模板", "body_template", profile?.body_template || "巡检报告已生成，请查看附件。")}
      ${checkField("SSL", "use_ssl", profile ? asBool(profile.use_ssl) : true)}
      ${checkField("STARTTLS", "use_starttls", profile ? asBool(profile.use_starttls) : false)}
      <div class="form-actions span-2">
        <button class="button" type="button" data-action="close-modal">取消</button>
        <button class="button primary" type="submit">${isEdit ? "保存修改" : "创建邮箱"}</button>
      </div>
    </form>
  `);

  const form = regions.modal.querySelector('[data-form="mail"]');
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitForm(form, isEdit ? `/mail-profiles/${profile.id}` : "/mail-profiles", isEdit ? "PUT" : "POST", isEdit ? "邮箱已更新" : "邮箱已创建");
  });
}

function openModal(title, body) {
  regions.modal.hidden = false;
  regions.modal.innerHTML = `
    <section class="modal-panel" role="dialog" aria-modal="true">
      <div class="modal-title">
        <h2>${escapeHtml(title)}</h2>
        <button class="modal-close-btn" type="button" data-action="close-modal" aria-label="关闭">&times;</button>
      </div>
      ${body}
    </section>
  `;
  bindDynamicActions(regions.modal);
  regions.modal.addEventListener("click", onModalBackdrop);
}

function onModalBackdrop(event) {
  if (event.target === regions.modal) {
    closeModal();
  }
}

function closeModal() {
  regions.modal.hidden = true;
  regions.modal.innerHTML = "";
  regions.modal.removeEventListener("click", onModalBackdrop);
}

async function submitForm(form, path, method, successMessage) {
  const submitButton = form.querySelector('button[type="submit"]');
  setButtonLoading(submitButton, true);
  try {
    await apiJson(path, { method, body: formToPayload(form) });
    closeModal();
    showSuccess(successMessage);
    await loadAll();
  } catch (error) {
    showError(error.message || "保存失败，请检查输入。");
  } finally {
    setButtonLoading(submitButton, false);
  }
}

async function deleteResource(path, successMessage) {
  const confirmed = await showConfirm({
    title: "确认删除",
    message: "您确定要删除该项配置吗？此操作不可撤销，请谨慎操作。",
    confirmText: "删除",
    cancelText: "取消",
    danger: true
  });
  if (!confirmed) {
    return;
  }
  try {
    await apiJson(path, { method: "DELETE" });
    showSuccess(successMessage);
    await loadAll();
  } catch (error) {
    showError(error.message || "删除失败。");
  }
}

async function runJob(jobId, btn = null) {
  setButtonLoading(btn, true);
  try {
    const result = await apiJson(`/jobs/${jobId}/run`, { method: "POST" });
    showSuccess(`任务已开始运行，运行记录 #${result.run_id}`);
    await loadAll();
    location.hash = viewToHash["runs"];
  } catch (error) {
    showError(error.message || "启动任务失败。");
  } finally {
    setButtonLoading(btn, false);
  }
}

async function testAuth(authId, btn = null) {
  setButtonLoading(btn, true);
  try {
    showToast("正在测试登录，请稍候...", "info");
    const result = await apiJson(`/auth-profiles/${authId}/test`, { method: "POST" });
    if (result.success) {
      showSuccess(result.message || "登录测试成功。");
    } else {
      showError(result.message || "登录测试失败。");
    }
  } catch (error) {
    showError(error.message || "测试登录失败。");
  } finally {
    setButtonLoading(btn, false);
  }
}

async function runPeriodic(reportType, btn = null) {
  setButtonLoading(btn, true);
  try {
    await apiJson(`/periodic-reports/${encodeURIComponent(reportType)}/run`, { method: "POST" });
    showSuccess("周期报告生成任务已启动。");
    await loadPeriodic(false);
  } catch (error) {
    showError(error.message || "启动周期报告失败。");
  } finally {
    setButtonLoading(btn, false);
  }
}

function formToPayload(form) {
  const payload = {};
  new FormData(form).forEach((value, key) => {
    payload[key] = value;
  });
  form.querySelectorAll('input[type="checkbox"]').forEach((input) => {
    payload[input.name] = input.checked;
  });
  return payload;
}

async function apiGet(path) {
  return apiJson(path, { method: "GET" });
}

async function apiJson(path, options = {}) {
  const url = apiUrl(path);
  const response = await fetch(url, {
    method: options.method || "GET",
    headers: { "Content-Type": "application/json", "Accept": "application/json" },
    credentials: "same-origin",
    body: options.body ? JSON.stringify(options.body) : undefined,
  });

  if (response.status === 401) {
    window.location.href = "/login";
    return null;
  }

  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof data === "object" ? data.detail : data;
    throw new Error(Array.isArray(detail) ? detail.map((item) => item.msg).join("；") : detail || "请求失败");
  }
  return data;
}

function statusBadge(status, label) {
  const value = String(status || "").toLowerCase();
  const className = value === "success" ? "ok" : value === "failed" ? "bad" : value === "partial_success" ? "warn" : value === "running" ? "info" : "";
  return `<span class="badge ${className}">${escapeHtml(label || status || "-")}</span>`;
}

function field(label, name, value = "", type = "text", required = false, span = false, placeholder = "", group = "") {
  return `
    <label class="${span ? "span-2" : ""}" ${group ? `data-auth-group="${escapeAttr(group)}"` : ""}>
      ${escapeHtml(label)}
      <input name="${escapeAttr(name)}" type="${escapeAttr(type)}" value="${escapeAttr(value)}" ${placeholder ? `placeholder="${escapeAttr(placeholder)}"` : ""} ${required ? "required" : ""}>
    </label>
  `;
}

function textareaField(label, name, value = "") {
  return `
    <label class="span-2">
      ${escapeHtml(label)}
      <textarea name="${escapeAttr(name)}" rows="5">${escapeHtml(value)}</textarea>
    </label>
  `;
}

function selectField(label, name, value, options) {
  const rendered = [`<option value="">不选择</option>`].concat(options.map(([optionValue, text]) => {
    const selected = String(optionValue) === String(value) ? "selected" : "";
    return `<option value="${escapeAttr(optionValue)}" ${selected}>${escapeHtml(text)}</option>`;
  })).join("");
  return `
    <label>
      ${escapeHtml(label)}
      <select name="${escapeAttr(name)}">${rendered}</select>
    </label>
  `;
}

function checkField(label, name, checked) {
  return `
    <label class="check">
      <input type="checkbox" name="${escapeAttr(name)}" ${checked ? "checked" : ""}>
      ${escapeHtml(label)}
    </label>
  `;
}

function loadingMetrics() {
  return Array.from({ length: 4 }, () => `
    <div>
      <span>加载中</span>
      <strong>--</strong>
    </div>
  `).join("");
}

function loadingTable(rows) {
  return `
    <table>
      <tbody>
        ${Array.from({ length: rows }, () => `<tr><td class="empty">加载中...</td></tr>`).join("")}
      </tbody>
    </table>
  `;
}

function renderError(region, error) {
  region.innerHTML = `<div class="empty">数据加载失败：${escapeHtml(error.message || "请稍后重试")}</div>`;
}

function renderGlobalError(error) {
  [regions.jobs, regions.recentRuns, regions.runs, regions.auth, regions.mail, regions.periodic].forEach((region) => renderError(region, error));
  regions.metrics.innerHTML = "";
  showError(error.message || "数据加载失败。");
}

function showToastMessage(message, type = "info") {
  showToast({ type, message });
}

function showToast(input = {}, legacyType = "info") {
  const options = typeof input === "string" ? { type: legacyType, message: input } : (input || {});
  const { type = "info", title = "", message = "", duration: rawDuration = 0 } = options;
  let duration = rawDuration;
  let realTitle = title;
  let realDesc = message;
  if (!realTitle && realDesc) {
    realTitle = realDesc;
    realDesc = "";
  }
  if (!realTitle && !realDesc) return;

  if (duration === 0) {
    duration = type === "error" ? 8000 : 3500;
  }

  const container = document.querySelector('[data-region="toasts"]');
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = `custom-toast toast-${type}`;

  let iconSvg = "";
  if (type === "success") {
    iconSvg = `<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd" /></svg>`;
  } else if (type === "error") {
    iconSvg = `<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clip-rule="evenodd" /></svg>`;
  } else if (type === "warning") {
    iconSvg = `<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 102 0V6a1 1 0 00-1-1z" clip-rule="evenodd" /></svg>`;
  } else {
    iconSvg = `<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zm-1 3a1 1 0 00-1 1v3a1 1 0 102 0v-3a1 1 0 00-1-1z" clip-rule="evenodd" /></svg>`;
  }

  toast.innerHTML = `
    <div class="toast-icon-wrapper">${iconSvg}</div>
    <div class="toast-content">
      <div class="toast-title">${escapeHtml(realTitle)}</div>
      ${realDesc ? `<div class="toast-desc">${escapeHtml(realDesc)}</div>` : ""}
    </div>
    <button class="toast-close-btn" type="button" aria-label="关闭">&times;</button>
    <div class="toast-progress">
      <div class="toast-progress-bar" style="transform: scaleX(1);"></div>
    </div>
  `;

  container.appendChild(toast);

  requestAnimationFrame(() => {
    toast.classList.add("show");
  });

  let isPaused = false;
  let timeLeft = duration;
  const tick = 30;
  const progressBar = toast.querySelector(".toast-progress-bar");

  const interval = setInterval(() => {
    if (isPaused) return;
    timeLeft -= tick;
    if (timeLeft <= 0) {
      clearInterval(interval);
      dismiss();
    } else {
      const percentage = timeLeft / duration;
      progressBar.style.transform = `scaleX(${percentage})`;
    }
  }, tick);

  function dismiss() {
    clearInterval(interval);
    toast.classList.remove("show");
    toast.classList.add("hide");
    setTimeout(() => {
      toast.remove();
    }, 300);
  }

  toast.querySelector(".toast-close-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    dismiss();
  });

  toast.addEventListener("mouseenter", () => {
    isPaused = true;
  });
  toast.addEventListener("mouseleave", () => {
    isPaused = false;
  });
}

function showSuccess(message) {
  showToast({ type: "success", message });
}

function showError(message) {
  showToast({ type: "error", message });
}

function showWarning(message) {
  showToast({ type: "warning", message });
}

function showInfo(message) {
  showToast({ type: "info", message });
}

function setButtonLoading(button, loading) {
  if (!button) return;
  if (loading) {
    button.classList.add("btn-loading");
    button.disabled = true;
  } else {
    button.classList.remove("btn-loading");
    button.disabled = false;
  }
}

function showConfirm({ title, message, confirmText = "确认", cancelText = "取消", danger = false }) {
  return new Promise((resolve) => {
    const confirmRoot = document.querySelector('[data-region="confirm"]');
    if (!confirmRoot) {
      resolve(false);
      return;
    }
    confirmRoot.hidden = false;

    const confirmBtnClass = danger ? "button danger" : "button primary";
    const iconSvg = danger
      ? `<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M10 6.75V10.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><path d="M10 13.35H10.01" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/><path d="M8.56 3.7L2.9 13.55A1.9 1.9 0 004.55 16.4h10.9a1.9 1.9 0 001.65-2.85L11.44 3.7a1.66 1.66 0 00-2.88 0z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>`
      : `<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M10 6.5V10.25" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><path d="M10 13.45H10.01" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/><circle cx="10" cy="10" r="7" stroke="currentColor" stroke-width="1.5"/></svg>`;

    confirmRoot.innerHTML = `
      <section class="confirm-panel ${danger ? "confirm-danger" : ""}" role="dialog" aria-modal="true">
        <div class="confirm-head">
          <div class="confirm-icon">${iconSvg}</div>
          <div class="confirm-copy">
            <div class="confirm-title">${escapeHtml(title)}</div>
            <div class="confirm-message">${escapeHtml(message)}</div>
          </div>
        </div>
        <div class="confirm-actions">
          <button type="button" class="button cancel-btn">${escapeHtml(cancelText)}</button>
          <button type="button" class="${confirmBtnClass} confirm-btn">${escapeHtml(confirmText)}</button>
        </div>
      </section>
    `;

    const cancelBtn = confirmRoot.querySelector(".cancel-btn");
    const confirmBtn = confirmRoot.querySelector(".confirm-btn");

    confirmBtn.focus();

    function close(result) {
      confirmRoot.hidden = true;
      confirmRoot.innerHTML = "";
      window.removeEventListener("keydown", onKeyDown);
      resolve(result);
    }

    function onKeyDown(event) {
      if (event.key === "Escape") {
        close(false);
      }
    }

    cancelBtn.addEventListener("click", () => close(false));
    confirmBtn.addEventListener("click", () => close(true));
    window.addEventListener("keydown", onKeyDown);
  });
}

function asBool(value) {
  if (typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    return value !== 0;
  }
  return ["1", "true", "yes", "on", "enabled"].includes(String(value || "").toLowerCase());
}

function numberText(value) {
  return Number(value || 0).toLocaleString("zh-CN");
}

function shortDate(value) {
  if (!value) {
    return "-";
  }
  return String(value).replace("T", " ").slice(0, 19);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}
