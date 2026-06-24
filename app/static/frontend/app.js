const state = {
  view: "dashboard",
  jobs: [],
  runs: [],
  authProfiles: [],
  mailProfiles: [],
  periodic: { settings: [], runs: [] },
  metrics: {},
  storage: { usage: {}, config: {}, cleanupRuns: [] }
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
  "mail": "#/mail",
  "storage": "#/storage"
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
  } else if (hash === "#/storage") {
    view = "storage";
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
  storage: document.querySelector('[data-region="storage-panel"]'),
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
  } else if (action === "refresh-storage") {
    loadStorage(true);
  } else if (action === "estimate-storage") {
    estimateStorage(triggerButton);
  } else if (action === "run-storage-cleanup") {
    runStorageCleanup(triggerButton);
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
  if (view === "storage") {
    loadStorage(false);
  }
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
    await loadStorage(false);

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
  if (regions.storage) {
    regions.storage.innerHTML = loadingTable(6);
  }
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

function tryParseCronToSimple(cron) {
  if (!cron) return null;
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return null;
  const [mPart, hPart, domPart, monPart, dowPart] = parts;
  if (domPart !== "*" || monPart !== "*") return null;
  
  let frequency = "daily";
  if (dowPart === "*" || dowPart === "?") {
    frequency = "daily";
  } else if (dowPart === "1-5" || dowPart === "mon-fri" || dowPart === "1,2,3,4,5") {
    frequency = "workday";
  } else {
    return null;
  }
  
  const hours = hPart.split(",").map(Number);
  const minutes = mPart.split(",").map(Number);
  if (hours.some(isNaN) || minutes.some(isNaN) || hours.length > 2) return null;
  
  if (hours.length === 1) {
    const h = hours[0];
    if (minutes.length !== 1) return null;
    const m = minutes[0];
    const timeStr = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
    if (h < 12) {
      return { frequency, morning_enabled: true, morning_time: timeStr, afternoon_enabled: false, afternoon_time: "17:05" };
    } else {
      return { frequency, morning_enabled: false, morning_time: "09:05", afternoon_enabled: true, afternoon_time: timeStr };
    }
  } else if (hours.length === 2) {
    const h1 = Math.min(...hours);
    const h2 = Math.max(...hours);
    if (h1 >= 12 || h2 < 12) return null;
    let m1, m2;
    if (minutes.length === 1) {
      m1 = m2 = minutes[0];
    } else if (minutes.length === 2) {
      m1 = minutes[0];
      m2 = minutes[1];
    } else {
      return null;
    }
    return {
      frequency,
      morning_enabled: true,
      morning_time: `${String(h1).padStart(2, '0')}:${String(m1).padStart(2, '0')}`,
      afternoon_enabled: true,
      afternoon_time: `${String(h2).padStart(2, '0')}:${String(m2).padStart(2, '0')}`
    };
  }
  return null;
}

function bindJobFormEvents(form) {
  if (!form) return;
  const modeSelect = form.querySelector('#job-schedule-mode-select');
  const simpleSec = form.querySelector('#simple-schedule-section');
  const cronSec = form.querySelector('#cron-schedule-section');
  
  function updateSections() {
    const mode = modeSelect.value;
    if (mode === "simple") {
      simpleSec.style.display = "grid";
      cronSec.style.display = "none";
      form.querySelector('[name="cron_expression"]').required = false;
    } else {
      simpleSec.style.display = "none";
      cronSec.style.display = "grid";
      form.querySelector('[name="cron_expression"]').required = true;
    }
  }
  
  modeSelect.addEventListener("change", updateSections);
  updateSections();
  
  const morningCheck = form.querySelector('[name="morning_enabled"]');
  const morningTimeInput = form.querySelector('[name="morning_time"]');
  const afternoonCheck = form.querySelector('[name="afternoon_enabled"]');
  const afternoonTimeInput = form.querySelector('[name="afternoon_time"]');
  
  function updateTimeInputs() {
    morningTimeInput.disabled = !morningCheck.checked;
    afternoonTimeInput.disabled = !afternoonCheck.checked;
  }
  morningCheck.addEventListener("change", updateTimeInputs);
  afternoonCheck.addEventListener("change", updateTimeInputs);
  updateTimeInputs();
}

function openJobModal(job = null) {
  const isEdit = Boolean(job);
  
  let scheduleMode = job?.schedule_mode || "simple";
  let sc = {};
  if (job?.schedule_config) {
    try { sc = JSON.parse(job.schedule_config); } catch(e) {}
  } else if (job?.cron_expression) {
    sc = tryParseCronToSimple(job.cron_expression) || {};
    if (Object.keys(sc).length === 0) {
      scheduleMode = "cron";
    }
  }
  
  const frequency = sc.frequency || "daily";
  const morningEnabled = sc.morning_enabled !== undefined ? sc.morning_enabled : true;
  const morningTime = sc.morning_time || "09:05";
  const afternoonEnabled = sc.afternoon_enabled !== undefined ? sc.afternoon_enabled : true;
  const afternoonTime = sc.afternoon_time || "17:05";

  const sendMailOnComplete = job ? asBool(job.send_mail_on_complete) : true;
  const sendMailOnError = job ? asBool(job.send_mail_on_error) : true;

  openModal(isEdit ? "修改巡检任务" : "新增巡检任务", `
    <form class="form-grid wide" data-form="job">
      ${field("任务名称", "name", job?.name || "", "text", true)}
      ${field("运行环境", "environment", job?.environment || "生产环境")}
      
      <div class="span-2" style="border-top: 1px solid #ddd; margin: 5px 0; padding-top: 10px;">
        <strong>定时巡检计划配置：</strong>
      </div>
      
      <label class="span-2">
        巡检定时模式
        <select name="schedule_mode" id="job-schedule-mode-select">
          <option value="simple" ${scheduleMode === "simple" ? "selected" : ""}>简易时间配置 (推荐)</option>
          <option value="cron" ${scheduleMode === "cron" ? "selected" : ""}>高级 Cron 表达式模式</option>
        </select>
      </label>
      
      <!-- 简易配置容器 -->
      <div id="simple-schedule-section" class="span-2 form-grid wide" style="padding: 0; gap: 15px;">
        <label class="span-2">
          巡检频率
          <select name="frequency">
            <option value="daily" ${frequency === "daily" ? "selected" : ""}>每天</option>
            <option value="workday" ${frequency === "workday" ? "selected" : ""}>周一到周五</option>
          </select>
        </label>
        
        <label class="check">
          <input type="checkbox" name="morning_enabled" ${morningEnabled ? "checked" : ""}>
          启用上午巡检
        </label>
        <label>
          上午时间
          <input type="time" name="morning_time" value="${escapeAttr(morningTime)}">
        </label>
        
        <label class="check">
          <input type="checkbox" name="afternoon_enabled" ${afternoonEnabled ? "checked" : ""}>
          启用下午巡检
        </label>
        <label>
          下午时间
          <input type="time" name="afternoon_time" value="${escapeAttr(afternoonTime)}">
        </label>
      </div>
      
      <!-- 高级 Cron 表达式配置 -->
      <div id="cron-schedule-section" class="span-2 form-grid wide" style="padding: 0; gap: 15px; display: none;">
        ${field("Cron 表达式", "cron_expression", job?.cron_expression || "0 9 * * *", "text", false)}
        ${field("计划说明 (选填)", "schedule_label", job?.schedule_label || "")}
      </div>

      <div class="span-2" style="border-top: 1px solid #ddd; margin: 5px 0; padding-top: 10px;">
        <strong>发信与浏览器配置：</strong>
      </div>
      
      ${selectField("邮件配置", "mail_profile_id", job?.mail_profile_id || "", state.mailProfiles.map((profile) => [profile.id, profile.name]))}
      ${field("时间范围", "time_range_label", job?.time_range_label || "最近 24 小时")}
      
      <label class="check">
        <input type="checkbox" name="send_mail_on_complete" ${sendMailOnComplete ? "checked" : ""}>
        巡检完成后发送邮件
      </label>
      <label class="check">
        <input type="checkbox" name="send_mail_on_error" ${sendMailOnError ? "checked" : ""}>
        巡检异常时发送邮件
      </label>
      
      <details class="advanced-settings span-2">
        <summary>高级浏览器设置</summary>
        <div class="form-grid wide advanced-grid">
          ${field("报告标题", "report_title", job?.report_title || "自动化巡检报告")}
          ${field("浏览器宽度", "browser_width", job?.browser_width || 1920, "number")}
          ${field("浏览器高度", "browser_height", job?.browser_height || 1080, "number")}
          ${checkField("无头浏览器", "headless", job ? asBool(job.headless) : true)}
        </div>
      </details>
      
      ${checkField("启用任务", "enabled", job ? asBool(job.enabled) : true)}
 
      <div class="form-actions span-2" style="margin-top: 10px;">
        <button class="button" type="button" data-action="close-modal">取消</button>
        <button class="button primary" type="submit">${isEdit ? "保存修改" : "创建任务"}</button>
      </div>
    </form>
  `);
 
  const form = regions.modal.querySelector('[data-form="job"]');
  bindJobFormEvents(form);
  
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

async function loadStorage(showToast = false) {
  if (!regions.storage) return;
  try {
    const data = await apiGet("/storage");
    state.storage.usage = data.usage || {};
    state.storage.config = data.config || {};
    
    const runsData = await apiGet("/storage/cleanup-runs");
    state.storage.cleanupRuns = runsData.runs || [];
    
    renderStorage();
    if (showToast) {
      showSuccess("存储状态数据已刷新");
    }
  } catch (error) {
    renderError(regions.storage, error);
  }
}

function renderStorage() {
  if (!regions.storage) return;
  const usage = state.storage.usage || {};
  const config = state.storage.config || {};
  const cleanupRuns = state.storage.cleanupRuns || [];
  
  let scheduleCfg = {};
  if (config.cleanup_schedule_config) {
    try {
      scheduleCfg = JSON.parse(config.cleanup_schedule_config);
    } catch (e) {
      console.error("解析清理计划配置失败:", e);
    }
  }
  const scheduleTime = scheduleCfg.time || "02:30";
  const dayOfWeek = scheduleCfg.day_of_week || "1";
  const dayOfMonth = scheduleCfg.day_of_month || "1";

  const storageMetricsHTML = `
    <div class="metrics-grid">
      <div>
        <span>Data 目录总占用</span>
        <strong>${formatBytes(usage.total_bytes || 0)}</strong>
      </div>
      <div>
        <span>巡检截图占用</span>
        <strong>${formatBytes(usage.screenshots_bytes || 0)}</strong>
      </div>
      <div>
        <span>Word 报告占用</span>
        <strong>${formatBytes(usage.reports_bytes || 0)}</strong>
      </div>
      <div>
        <span>数据库文件大小</span>
        <strong>${formatBytes(usage.sqlite_db_bytes || 0)}</strong>
      </div>
    </div>
  `;
  
  const detailsHTML = `
    <div class="card-grid" style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 20px;">
      <div class="panel">
        <div class="panel-header">
          <h3>物理目录细分占用</h3>
        </div>
        <table>
          <tbody>
            <tr><td>周期汇总 Zip 报告</td><td><strong>${formatBytes(usage.periodic_reports_bytes || 0)}</strong></td></tr>
            <tr><td>历史运行日志文件</td><td><strong>${formatBytes(usage.logs_bytes || 0)}</strong></td></tr>
            <tr><td>浏览器 session 缓存</td><td><strong>${formatBytes(usage.browser_state_bytes || 0)}</strong></td></tr>
          </tbody>
        </table>
        
        <div class="panel-header" style="margin-top: 20px;">
          <h3>空间回收操作</h3>
        </div>
        <div style="padding: 10px 0;">
          <p class="muted">预估可清理空间: <strong class="ok-text" id="est-bytes-text">${formatBytes(usage.estimated_cleanup_bytes || 0)}</strong> (共 <strong id="est-files-text">${usage.estimated_cleanup_files || 0}</strong> 个文件)</p>
          <div class="action-row" style="margin-top: 10px;">
            <button class="button" type="button" data-action="estimate-storage">预估可清理空间</button>
            <button class="button danger" type="button" data-action="run-storage-cleanup">立即执行清理</button>
          </div>
        </div>
      </div>
      
      <div class="panel">
        <div class="panel-header">
          <h3>数据保留与自动清理策略配置</h3>
        </div>
        <form class="form-grid" data-form="storage-settings">
          <label class="check span-2">
            <input type="checkbox" name="enabled" ${asBool(config.enabled) ? "checked" : ""}>
            开启自动定时清理任务
          </label>
          
          <label>
            自动清理频率
            <select name="cleanup_schedule_mode">
              <option value="daily" ${config.cleanup_schedule_mode === "daily" ? "selected" : ""}>每天</option>
              <option value="weekly" ${config.cleanup_schedule_mode === "weekly" ? "selected" : ""}>每周</option>
              <option value="monthly" ${config.cleanup_schedule_mode === "monthly" ? "selected" : ""}>每月</option>
            </select>
          </label>
          
          <label>
            清理触发时间
            <input type="time" name="schedule_time" value="${escapeAttr(scheduleTime)}" required>
          </label>
          
          <label id="storage-dow-label">
            选择星期几
            <select name="schedule_day_of_week">
              <option value="1" ${String(dayOfWeek) === "1" ? "selected" : ""}>周一</option>
              <option value="2" ${String(dayOfWeek) === "2" ? "selected" : ""}>周二</option>
              <option value="3" ${String(dayOfWeek) === "3" ? "selected" : ""}>周三</option>
              <option value="4" ${String(dayOfWeek) === "4" ? "selected" : ""}>周四</option>
              <option value="5" ${String(dayOfWeek) === "5" ? "selected" : ""}>周五</option>
              <option value="6" ${String(dayOfWeek) === "6" ? "selected" : ""}>周六</option>
              <option value="7" ${String(dayOfWeek) === "7" ? "selected" : ""}>周日</option>
            </select>
          </label>
          
          <label id="storage-dom-label">
            选择几号触发 (1-31)
            <input type="number" name="schedule_day_of_month" min="1" max="31" value="${escapeAttr(dayOfMonth)}">
          </label>
          
          <label class="check span-2">
            <input type="checkbox" name="allow_manual_cleanup" ${asBool(config.allow_manual_cleanup) ? "checked" : ""}>
            允许前端手动触发“立即清理”
          </label>
          
          <div class="span-2" style="border-top: 1px solid #ddd; margin: 10px 0; padding-top: 10px;">
            <strong>各类数据保留天数：</strong>
          </div>
          
          <label>
            已发送周期报告 (天)
            <input type="number" name="periodic_sent_retention_days" min="1" value="${config.periodic_sent_retention_days || 3}" required>
          </label>
          
          <label>
            发送失败周期报告 (天)
            <input type="number" name="periodic_failed_retention_days" min="1" value="${config.periodic_failed_retention_days || 30}" required>
          </label>
          
          <label>
            普通巡检截图 (天)
            <input type="number" name="screenshot_retention_days" min="1" value="${config.screenshot_retention_days || 7}" required>
          </label>
          
          <label>
            普通巡检 Word 报告 (天)
            <input type="number" name="report_retention_days" min="1" value="${config.report_retention_days || 30}" required>
          </label>
          
          <label>
            运行日志文件 (天)
            <input type="number" name="log_retention_days" min="1" value="${config.log_retention_days || 14}" required>
          </label>
          
          <label>
            运行记录数据库数据 (天)
            <input type="number" name="run_record_retention_days" min="1" value="${config.run_record_retention_days || 90}" required>
          </label>
          
          <label class="check span-2">
            <input type="checkbox" name="browser_state_cleanup_enabled" ${asBool(config.browser_state_cleanup_enabled) ? "checked" : ""}>
            启用自动清理浏览器 session 缓存
          </label>
          
          <label id="storage-browser-retention-label">
            浏览器 session 缓存 (天)
            <input type="number" name="browser_state_retention_days" min="1" value="${config.browser_state_retention_days || 30}" required>
          </label>
          
          <label>
            数据安全保护天数 (天)
            <input type="number" name="protect_recent_days" min="1" value="${config.protect_recent_days || 3}" required>
            <span class="muted" style="font-size: 11px; display: block; margin-top: 4px;">近这几天内的任何数据决不物理删除。</span>
          </label>
          
          <div class="form-actions span-2" style="margin-top: 15px;">
            <button class="button primary" type="submit">保存保留策略配置</button>
          </div>
        </form>
      </div>
    </div>
  `;

  const historyHTML = `
    <div class="panel" style="margin-top: 20px;">
      <div class="panel-header">
        <h2>清理日志与最近清理结果</h2>
      </div>
      ${cleanupRuns.length ? `
        <table>
          <thead>
            <tr>
              <th>编号</th>
              <th>触发模式</th>
              <th>执行状态</th>
              <th>开始时间</th>
              <th>删除文件数</th>
              <th>释放空间</th>
              <th>清理运行记录数</th>
              <th>说明 / 失败日志</th>
            </tr>
          </thead>
          <tbody>
            ${cleanupRuns.map((run) => `
              <tr>
                <td>#${run.id}</td>
                <td><code>${run.mode === "auto" ? "定时自动" : run.mode === "manual" ? "手动触发" : "空间预估"}</code></td>
                <td>${statusBadge(run.status, run.status === "success" ? "成功" : run.status === "partial_success" ? "部分成功" : "失败")}</td>
                <td>${escapeHtml(shortDate(run.started_at))}</td>
                <td>${numberText(run.deleted_files_count)} 个</td>
                <td><strong>${formatBytes(run.deleted_bytes)}</strong></td>
                <td>${numberText(run.deleted_run_records_count)} 条</td>
                <td style="max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeAttr(run.error_summary || "")}">
                  ${run.error_summary ? `<span class="bad-text">${escapeHtml(run.error_summary)}</span>` : '<span class="ok-text">执行完成，无异常</span>'}
                </td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      ` : `<div class="empty">暂无清理任务执行历史。</div>`}
    </div>
  `;

  regions.storage.innerHTML = storageMetricsHTML + detailsHTML + historyHTML;
  
  const form = regions.storage.querySelector('[data-form="storage-settings"]');
  bindStorageFormEvents(form);
  bindDynamicActions(regions.storage);
}

function bindStorageFormEvents(form) {
  if (!form) return;
  
  const modeSelect = form.querySelector('[name="cleanup_schedule_mode"]');
  const dowLabel = form.querySelector('#storage-dow-label');
  const domLabel = form.querySelector('#storage-dom-label');
  const bsCheckbox = form.querySelector('[name="browser_state_cleanup_enabled"]');
  const bsLabel = form.querySelector('#storage-browser-retention-label');

  function updateScheduleFields() {
    const mode = modeSelect.value;
    if (mode === "daily") {
      dowLabel.style.display = "none";
      domLabel.style.display = "none";
    } else if (mode === "weekly") {
      dowLabel.style.display = "";
      domLabel.style.display = "none";
    } else if (mode === "monthly") {
      dowLabel.style.display = "none";
      domLabel.style.display = "";
    }
  }

  function updateBrowserFields() {
    const enabled = bsCheckbox.checked;
    const input = bsLabel.querySelector('input');
    input.disabled = !enabled;
    if (!enabled) {
      bsLabel.style.opacity = "0.5";
    } else {
      bsLabel.style.opacity = "1";
    }
  }

  modeSelect.addEventListener("change", updateScheduleFields);
  bsCheckbox.addEventListener("change", updateBrowserFields);
  
  updateScheduleFields();
  updateBrowserFields();

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submitButton = form.querySelector('button[type="submit"]');
    setButtonLoading(submitButton, true);
    
    try {
      const payload = formToPayload(form);
      
      const scheduleConfig = {
        time: form.querySelector('[name="schedule_time"]').value || "02:30"
      };
      if (payload.cleanup_schedule_mode === "weekly") {
        scheduleConfig.day_of_week = form.querySelector('[name="schedule_day_of_week"]').value || "1";
      } else if (payload.cleanup_schedule_mode === "monthly") {
        scheduleConfig.day_of_month = form.querySelector('[name="schedule_day_of_month"]').value || "1";
      }
      
      payload.cleanup_schedule_config = JSON.stringify(scheduleConfig);
      
      delete payload.schedule_time;
      delete payload.schedule_day_of_week;
      delete payload.schedule_day_of_month;
      
      await apiJson("/storage/settings", { method: "PUT", body: payload });
      showSuccess("数据保留策略配置保存成功！");
      await loadStorage(false);
    } catch (error) {
      showError(error.message || "配置保存失败，请检查输入参数。");
    } finally {
      setButtonLoading(submitButton, false);
    }
  });
}

async function estimateStorage(btn) {
  setButtonLoading(btn, true);
  try {
    showToast("正在预估清理空间，请稍候...", "info");
    const result = await apiJson("/storage/estimate", { method: "POST" });
    if (result.ok && result.result) {
      const res = result.result;
      const bytesEl = document.getElementById("est-bytes-text");
      const filesEl = document.getElementById("est-files-text");
      if (bytesEl) bytesEl.innerText = formatBytes(res.deleted_bytes);
      if (filesEl) filesEl.innerText = String(res.deleted_files_count);
      showSuccess(`预估成功！可释放空间约 ${formatBytes(res.deleted_bytes)}，文件数 ${res.deleted_files_count} 个`);
    } else {
      showError("预估空间返回失败。");
    }
  } catch (error) {
    showError(error.message || "空间预估失败。");
  } finally {
    setButtonLoading(btn, false);
  }
}

async function runStorageCleanup(btn) {
  const confirmed = await showConfirm({
    title: "安全警告 - 立即执行清理",
    message: "您确定要立刻物理删除所有超出保留天数的数据文件吗？本操作将永久清理相关截图、报告和日志且不可恢复。请确认操作！",
    confirmText: "立即清理",
    cancelText: "取消",
    danger: true
  });
  if (!confirmed) return;

  setButtonLoading(btn, true);
  try {
    showToast("清理任务已开始执行，物理清扫中...", "info");
    const result = await apiJson("/storage/cleanup", { method: "POST" });
    if (result.ok && result.result) {
      const res = result.result;
      showSuccess(`物理清理执行完成！共成功删除文件数 ${res.deleted_files_count} 个，释放物理空间 ${formatBytes(res.deleted_bytes)}`);
      await loadStorage(false);
    } else {
      showError("清理执行返回异常。");
    }
  } catch (error) {
    showError(error.message || "立即清理失败，可能被系统安全策略拦截。");
  } finally {
    setButtonLoading(btn, false);
  }
}

function formatBytes(bytes) {
  if (bytes === undefined || bytes === null || isNaN(bytes)) return "-";
  if (bytes === 0) return "0 Bytes";
  const k = 1024;
  const sizes = ["Bytes", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

