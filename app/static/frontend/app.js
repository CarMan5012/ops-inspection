const state = {
  view: "dashboard",
  jobs: [],
  runs: [],
  authProfiles: [],
  mailProfiles: [],
  periodic: { settings: [], runs: [] },
  metrics: {},
  storage: { usage: {}, config: {}, cleanupRuns: [], swagger: { enabled: false }, security: {} }
};

let silentRefreshTimer = null;

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

function icon(name, className = "") {
  return `<i data-lucide="${escapeAttr(name)}"${className ? ` class="${escapeAttr(className)}"` : ""}></i>`;
}

function renderIcons(root = document) {
  if (window.lucide && typeof window.lucide.createIcons === "function") {
    window.lucide.createIcons({
      attrs: {
        "aria-hidden": "true",
        "stroke-width": "1.8"
      },
      nameAttr: "data-lucide",
      root
    });
  }
}

const QR_ECC_CODEWORDS_LOW = [
  0, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28,
  28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30
];

const QR_NUM_BLOCKS_LOW = [
  0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7,
  8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25
];

function initQrGaloisField() {
  const exp = new Array(255);
  const log = new Array(256).fill(0);
  let value = 1;
  for (let i = 0; i < 255; i++) {
    exp[i] = value;
    log[value] = i;
    value <<= 1;
    if (value & 0x100) value ^= 0x11D;
  }
  return { exp, log };
}

const QR_GF = initQrGaloisField();

function qrMultiply(x, y) {
  if (x === 0 || y === 0) return 0;
  return QR_GF.exp[(QR_GF.log[x] + QR_GF.log[y]) % 255];
}

function qrRawDataModules(version) {
  let result = (16 * version + 128) * version + 64;
  if (version >= 2) {
    const numAlign = Math.floor(version / 7) + 2;
    result -= (25 * numAlign - 10) * numAlign - 55;
    if (version >= 7) result -= 36;
  }
  return result;
}

function qrDataCodewords(version) {
  const rawCodewords = Math.floor(qrRawDataModules(version) / 8);
  return rawCodewords - QR_ECC_CODEWORDS_LOW[version] * QR_NUM_BLOCKS_LOW[version];
}

function qrAppendBits(buffer, value, length) {
  for (let i = length - 1; i >= 0; i--) {
    buffer.push((value >>> i) & 1);
  }
}

function qrBuildDataCodewords(text, version) {
  const bytes = Array.from(new TextEncoder().encode(text));
  const capacityBits = qrDataCodewords(version) * 8;
  const countBits = version < 10 ? 8 : 16;
  const bits = [];
  qrAppendBits(bits, 0x4, 4);
  qrAppendBits(bits, bytes.length, countBits);
  bytes.forEach((byte) => qrAppendBits(bits, byte, 8));
  qrAppendBits(bits, 0, Math.min(4, capacityBits - bits.length));
  while (bits.length % 8 !== 0) bits.push(0);

  const padBytes = [0xEC, 0x11];
  let padIndex = 0;
  while (bits.length < capacityBits) {
    qrAppendBits(bits, padBytes[padIndex % 2], 8);
    padIndex += 1;
  }

  const codewords = [];
  for (let i = 0; i < bits.length; i += 8) {
    let value = 0;
    for (let j = 0; j < 8; j++) value = (value << 1) | bits[i + j];
    codewords.push(value);
  }
  return codewords;
}

function qrFindVersion(text) {
  const byteLength = new TextEncoder().encode(text).length;
  for (let version = 1; version <= 40; version++) {
    const countBits = version < 10 ? 8 : 16;
    if (byteLength < (1 << countBits) && 4 + countBits + byteLength * 8 <= qrDataCodewords(version) * 8) {
      return version;
    }
  }
  throw new Error("二维码内容过长，无法生成");
}

function qrGeneratorPolynomial(degree) {
  const result = new Array(degree).fill(0);
  result[degree - 1] = 1;
  let root = 1;
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < result.length; j++) {
      result[j] = qrMultiply(result[j], root);
      if (j + 1 < result.length) result[j] ^= result[j + 1];
    }
    root = qrMultiply(root, 0x02);
  }
  return result;
}

function qrRemainder(data, generator) {
  const result = new Array(generator.length).fill(0);
  data.forEach((byte) => {
    const factor = byte ^ result.shift();
    result.push(0);
    for (let i = 0; i < result.length; i++) {
      result[i] ^= qrMultiply(generator[i], factor);
    }
  });
  return result;
}

function qrInterleaveBlocks(dataCodewords, version) {
  const rawCodewords = Math.floor(qrRawDataModules(version) / 8);
  const blockCount = QR_NUM_BLOCKS_LOW[version];
  const blockEccLength = QR_ECC_CODEWORDS_LOW[version];
  const generator = qrGeneratorPolynomial(blockEccLength);
  const shortBlockCount = blockCount - (rawCodewords % blockCount);
  const shortBlockDataLength = Math.floor(rawCodewords / blockCount) - blockEccLength;
  const blocks = [];
  let offset = 0;

  for (let i = 0; i < blockCount; i++) {
    const dataLength = shortBlockDataLength + (i < shortBlockCount ? 0 : 1);
    const data = dataCodewords.slice(offset, offset + dataLength);
    offset += dataLength;
    blocks.push({ data, ecc: qrRemainder(data, generator) });
  }

  const result = [];
  const maxDataLength = Math.max(...blocks.map((block) => block.data.length));
  for (let i = 0; i < maxDataLength; i++) {
    blocks.forEach((block) => {
      if (i < block.data.length) result.push(block.data[i]);
    });
  }
  for (let i = 0; i < blockEccLength; i++) {
    blocks.forEach((block) => result.push(block.ecc[i]));
  }
  return result;
}

function qrAlignmentPositions(version) {
  if (version === 1) return [];
  const size = version * 4 + 17;
  const count = Math.floor(version / 7) + 2;
  const step = version === 32 ? 26 : Math.ceil((size - 13) / (count * 2 - 2)) * 2;
  const result = [6];
  for (let pos = size - 7; result.length < count; pos -= step) {
    result.splice(1, 0, pos);
  }
  return result;
}

function qrBchFormatBits(mask) {
  const data = (1 << 3) | mask;
  let rem = data;
  for (let i = 0; i < 10; i++) {
    rem = (rem << 1) ^ (((rem >>> 9) & 1) * 0x537);
  }
  return ((data << 10) | (rem & 0x3FF)) ^ 0x5412;
}

function qrBchVersionBits(version) {
  let rem = version;
  for (let i = 0; i < 12; i++) {
    rem = (rem << 1) ^ (((rem >>> 11) & 1) * 0x1F25);
  }
  return (version << 12) | (rem & 0xFFF);
}

function qrMask(mask, x, y) {
  if (mask === 0) return (x + y) % 2 === 0;
  return false;
}

function qrCreateMatrix(text) {
  const version = qrFindVersion(text);
  const size = version * 4 + 17;
  const modules = Array.from({ length: size }, () => Array(size).fill(null));

  const set = (x, y, dark) => {
    if (x >= 0 && x < size && y >= 0 && y < size) modules[y][x] = Boolean(dark);
  };

  const drawFinder = (cx, cy) => {
    for (let dy = -4; dy <= 4; dy++) {
      for (let dx = -4; dx <= 4; dx++) {
        const distance = Math.max(Math.abs(dx), Math.abs(dy));
        set(cx + dx, cy + dy, distance !== 2 && distance !== 4);
      }
    }
  };

  const drawAlignment = (cx, cy) => {
    for (let dy = -2; dy <= 2; dy++) {
      for (let dx = -2; dx <= 2; dx++) {
        set(cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
      }
    }
  };

  const drawFormatBits = (mask) => {
    const bits = qrBchFormatBits(mask);
    for (let i = 0; i <= 5; i++) set(8, i, (bits >>> i) & 1);
    set(8, 7, (bits >>> 6) & 1);
    set(8, 8, (bits >>> 7) & 1);
    set(7, 8, (bits >>> 8) & 1);
    for (let i = 9; i < 15; i++) set(14 - i, 8, (bits >>> i) & 1);
    for (let i = 0; i < 8; i++) set(size - 1 - i, 8, (bits >>> i) & 1);
    for (let i = 8; i < 15; i++) set(8, size - 15 + i, (bits >>> i) & 1);
    set(8, size - 8, true);
  };

  const drawVersionBits = () => {
    if (version < 7) return;
    const bits = qrBchVersionBits(version);
    for (let i = 0; i < 18; i++) {
      const bit = (bits >>> i) & 1;
      const a = size - 11 + (i % 3);
      const b = Math.floor(i / 3);
      set(a, b, bit);
      set(b, a, bit);
    }
  };

  drawFinder(3, 3);
  drawFinder(size - 4, 3);
  drawFinder(3, size - 4);

  for (let i = 8; i < size - 8; i++) {
    set(i, 6, i % 2 === 0);
    set(6, i, i % 2 === 0);
  }

  const alignPositions = qrAlignmentPositions(version);
  alignPositions.forEach((x) => {
    alignPositions.forEach((y) => {
      const overlapsFinder =
        (x <= 8 && y <= 8) ||
        (x >= size - 9 && y <= 8) ||
        (x <= 8 && y >= size - 9);
      if (!overlapsFinder) drawAlignment(x, y);
    });
  });

  drawFormatBits(0);
  drawVersionBits();

  const dataCodewords = qrBuildDataCodewords(text, version);
  const codewords = qrInterleaveBlocks(dataCodewords, version);
  let bitIndex = 0;
  let upward = true;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right -= 1;
    for (let vert = 0; vert < size; vert++) {
      const y = upward ? size - 1 - vert : vert;
      for (let j = 0; j < 2; j++) {
        const x = right - j;
        if (modules[y][x] !== null) continue;
        const byte = codewords[Math.floor(bitIndex / 8)] || 0;
        let dark = ((byte >>> (7 - (bitIndex % 8))) & 1) === 1;
        if (qrMask(0, x, y)) dark = !dark;
        set(x, y, dark);
        bitIndex += 1;
      }
    }
    upward = !upward;
  }

  drawFormatBits(0);
  drawVersionBits();

  return modules.map((row) => row.map((cell) => Boolean(cell)));
}

function drawQrCanvas(canvas, value, pixelSize = 184) {
  if (!canvas || !value) return;
  const matrix = qrCreateMatrix(value);
  const quietZone = 4;
  const cells = matrix.length + quietZone * 2;
  const scale = Math.max(2, Math.floor(pixelSize / cells));
  const size = cells * scale;
  canvas.width = size;
  canvas.height = size;
  canvas.style.width = `${pixelSize}px`;
  canvas.style.height = `${pixelSize}px`;
  const context = canvas.getContext("2d");
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, size, size);
  context.fillStyle = "#111827";
  matrix.forEach((row, y) => {
    row.forEach((dark, x) => {
      if (dark) context.fillRect((x + quietZone) * scale, (y + quietZone) * scale, scale, scale);
    });
  });
}

function renderQrCodes(root = document) {
  root.querySelectorAll("canvas[data-qr-value]").forEach((canvas) => {
    try {
      drawQrCanvas(canvas, canvas.dataset.qrValue || "");
      canvas.removeAttribute("data-qr-value");
    } catch (error) {
      const fallback = document.createElement("div");
      fallback.className = "mfa-qr-error";
      fallback.textContent = "二维码生成失败，请使用下方密钥手动绑定。";
      canvas.replaceWith(fallback);
      console.error("MFA QR render failed", error);
    }
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  if (window.APP_CONFIG) {
    config = { ...config, ...window.APP_CONFIG };
  } else {
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
  }
  bindStaticActions(document);
  renderIcons();
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
  renderIcons(root);
}

function bindDynamicActions(root) {
  root.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", (event) => {
      handleAction(button.dataset.action, button.dataset.id, button);
      event.stopPropagation();
    });
  });
  renderIcons(root);
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
  } else if (action === "enable-swagger") {
    updateSwaggerSettings(true, triggerButton);
  } else if (action === "disable-swagger") {
    updateSwaggerSettings(false, triggerButton);
  } else if (action === "generate-mfa-secret") {
    generateMfaSecret(triggerButton);
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
    checkAndScheduleSilentRefresh();
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
    checkAndScheduleSilentRefresh();
  } catch (error) {
    renderError(regions.periodic, error);
  }
}

function checkAndScheduleSilentRefresh() {
  const hasRunning = state.runs.some(run => run.status === 'running') || 
                     (state.periodic.runs || []).some(run => run.status === 'running');
  
  if (hasRunning) {
    if (!silentRefreshTimer) {
      silentRefreshTimer = setInterval(refreshAllSilently, 4000);
    }
  } else {
    if (silentRefreshTimer) {
      clearInterval(silentRefreshTimer);
      silentRefreshTimer = null;
    }
  }
}

async function refreshAllSilently() {
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
    
    state.periodic = await apiGet("/periodic-reports");
    renderPeriodic();
    
    checkAndScheduleSilentRefresh();
  } catch (error) {
    console.error("静默刷新数据失败:", error);
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
    ["巡检任务", state.metrics.jobs_total || 0],
    ["启用任务", state.metrics.jobs_enabled || 0],
    ["成功运行", state.metrics.runs_success || 0],
    ["失败运行", state.metrics.runs_failed || 0],
  ];
  regions.metrics.innerHTML = items.map(([label, value]) => `
    <div>
      <span>${escapeHtml(label)}</span>
      <strong>${Number(value).toLocaleString("zh-CN")}</strong>
    </div>
  `).join("");
}

function renderJobs() {
  if (!state.jobs.length) {
    regions.jobs.innerHTML = `<div class="empty">还没有巡检任务，点击右上角新建任务开始配置。</div>`;
    return;
  }

  regions.jobs.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>任务名称</th>
          <th>运行环境</th>
          <th>巡检时间</th>
          <th>邮件通知</th>
          <th>状态</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        ${state.jobs.map((job) => `
          <tr>
            <td>
              <strong><a href="${route.jobDetail(job.id)}">${escapeHtml(job.name || "未命名任务")}</a></strong>
              ${job.load_error ? `<div class="inline-error">加载失败：${escapeHtml(job.load_error)}</div>` : ""}
            </td>
            <td>${escapeHtml(job.environment || "-")}</td>
            <td><code>${escapeHtml(job.schedule_label || job.cron_expression || "-")}</code></td>
            <td>${asBool(job.send_mail) ? "开启" : "关闭"}</td>
            <td>${asBool(job.enabled) ? '<span class="badge ok">启用</span>' : '<span class="badge">停用</span>'}</td>
            <td>
              <div class="action-row">
                <button type="button" class="button" data-action="run-job" data-id="${job.id}">${icon("play")}立即运行</button>
                <button type="button" class="button" data-action="edit-job" data-id="${job.id}">${icon("settings-2")}编辑</button>
                <a class="button" href="${route.jobDetail(job.id)}">${icon("panel-right-open")}详情</a>
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
    region.innerHTML = `<div class="empty">暂无运检记录。</div>`;
    renderIcons(region);
    return;
  }

  region.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>运行 ID</th>
          <th>任务名称</th>
          <th>状态</th>
          <th>开始时间</th>
          <th>成功截图</th>
          <th>失败截图</th>
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
                <a class="button" href="${route.runDetail(run.id)}">${icon("panel-right-open")}详情</a>
                ${run.report_url ? `<a class="button primary" href="${escapeAttr(run.report_url)}">${icon("download")}下载报告</a>` : ""}
              </div>
            </td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
  renderIcons(region);
}

function renderAuthProfiles() {
  const authTable = state.authProfiles.length ? `
    <table>
      <thead>
        <tr>
          <th>名称</th>
          <th>登录方式</th>
          <th>登录地址</th>
          <th>密码状态</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        ${state.authProfiles.map((profile) => `
          <tr>
            <td><strong>${escapeHtml(profile.name || "未命名配置")}</strong></td>
            <td>${profile.auth_type === "none" ? "无需登录" : "账号登录"}</td>
            <td><span class="url-text">${escapeHtml(profile.login_url || "-")}</span></td>
            <td>${profile.has_password ? '<span class="badge ok">已保存</span>' : '<span class="badge">未保存</span>'}</td>
            <td>
              <div class="action-row">
                <button class="button" type="button" data-action="edit-auth" data-id="${profile.id}">${icon("settings-2")}编辑</button>
                ${profile.auth_type === "form" ? `<button class="button" type="button" data-action="test-auth" data-id="${profile.id}">${icon("log-in")}测试登录</button>` : ""}
                <button class="button danger" type="button" data-action="delete-auth" data-id="${profile.id}">${icon("trash-2")}删除</button>
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
            <td><strong>${escapeHtml(profile.name || "未命名配置")}</strong></td>
            <td><code>${escapeHtml(profile.smtp_host || "-")}:${escapeHtml(profile.smtp_port || "-")}</code></td>
            <td>${escapeHtml(profile.sender || profile.username || "-")}</td>
            <td>${escapeHtml(profile.recipients || "-")}</td>
            <td>
              <div class="action-row">
                <button class="button" type="button" data-action="edit-mail" data-id="${profile.id}">${icon("settings-2")}编辑</button>
                <button class="button danger" type="button" data-action="delete-mail" data-id="${profile.id}">${icon("trash-2")}删除</button>
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

function reportTypeLabel(type) {
  if (type === "weekly") return "周报";
  if (type === "monthly") return "月报";
  return type || "-";
}

function renderPeriodic() {
  const settings = state.periodic.settings || [];
  const runs = state.periodic.runs || [];

  const settingsTable = settings.length ? `
    <table>
      <thead>
        <tr>
          <th>报告类型</th>
          <th>配置名称</th>
          <th>发送时间</th>
          <th>状态</th>
          <th>内容</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        ${settings.map((item) => `
          <tr>
            <td><span class="badge info">${escapeHtml(reportTypeLabel(item.report_type))}</span></td>
            <td><strong>${escapeHtml(item.name || "-")}</strong></td>
            <td><code>${escapeHtml(item.schedule_label || item.cron_expression || "-")}</code></td>
            <td>${asBool(item.enabled) ? '<span class="badge ok">启用</span>' : '<span class="badge">停用</span>'}</td>
            <td>${asBool(item.include_docx) ? "Word 文档" : ""}${asBool(item.include_screenshots) ? " 截图" : ""}</td>
            <td>
              <div class="action-row">
                <button class="button" type="button" data-action="run-periodic" data-id="${escapeAttr(item.report_type)}">${icon("play")}立即生成</button>
                <a class="button" href="${route.periodicEdit(item.report_type)}">${icon("settings-2")}编辑</a>
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
            <td>${escapeHtml(reportTypeLabel(run.report_type))}</td>
            <td>${escapeHtml(run.period_start || "-")} - ${escapeHtml(run.period_end || "-")}</td>
            <td>${statusBadge(run.status, run.status)}</td>
            <td>${escapeHtml(run.size_str || "-")}</td>
            <td>${escapeHtml(shortDate(run.finished_at))}</td>
            <td>${run.download_url ? `<a class="button" href="${escapeAttr(run.download_url)}">${icon("download")}下载</a>` : "-"}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  ` : `<div class="empty">暂无生成记录。</div>`;

  regions.periodic.innerHTML = `
    <div class="section-title"><h2>报告配置</h2></div>
    ${settingsTable}
    <div class="section-title"><h2>生成记录</h2></div>
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
          <option value="cron" ${scheduleMode === "cron" ? "selected" : ""}>高级时间规则模式</option>
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
      
      <!-- 高级时间规则配置 -->
      <div id="cron-schedule-section" class="span-2 form-grid wide" style="padding: 0; gap: 15px; display: none;">
        ${field("高级时间规则", "cron_expression", job?.cron_expression || "0 9 * * *", "text", false)}
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

      <div class="span-2" style="border-top: 1px solid #ddd; margin: 5px 0; padding-top: 10px;">
        <strong>钉钉推送配置：</strong>
      </div>
      <label class="check span-2">
        <input type="checkbox" name="dingtalk_enabled" id="spa_dingtalk_enabled_chk" ${job && asBool(job.dingtalk_enabled) ? "checked" : ""}>
        启用钉钉群组推送
      </label>
      <div id="spa_dingtalk_fields" class="span-2 form-grid wide" style="padding: 0; gap: 15px; grid-template-columns: 1fr 1fr;">
        <label class="span-2">
          机器人地址
          <input name="dingtalk_webhook" id="spa_dingtalk_webhook_input" value="" placeholder="${job?.has_dingtalk_webhook ? "已保存，留空沿用原地址或使用全局 DINGTALK_WEBHOOK" : "选填，留空则使用全局 DINGTALK_WEBHOOK"}">
        </label>
        <label>
          加签密钥
          <input name="dingtalk_secret" value="" placeholder="${job?.has_dingtalk_secret ? "已保存，留空沿用原密钥或使用全局 DINGTALK_SECRET" : "选填，留空则使用全局 DINGTALK_SECRET"}">
        </label>
        <label>
          自定义关键词
          <input name="dingtalk_keyword" value="${escapeAttr(job?.dingtalk_keyword || '')}" placeholder="选填，留空则使用全局 DINGTALK_KEYWORD">
        </label>
      </div>
      
      <details class="advanced-settings span-2" style="margin-top: 10px;">
        <summary>高级浏览器设置</summary>
        <div class="form-grid wide advanced-grid">
          ${field("报告标题", "report_title", job?.report_title || "自动化巡检报告")}
          ${field("浏览器宽度", "browser_width", job?.browser_width || 1920, "number")}
          ${field("浏览器高度", "browser_height", job?.browser_height || 1080, "number")}
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

  const dtCheck = form.querySelector('#spa_dingtalk_enabled_chk');
  const dtWebhook = form.querySelector('#spa_dingtalk_webhook_input');
  const dtFields = form.querySelector('#spa_dingtalk_fields');
  if (dtCheck && dtWebhook && dtFields) {
    const updateDingtalkFields = () => {
      if (dtCheck.checked) {
        dtFields.style.opacity = '1';
        if (!job?.has_dingtalk_webhook) {
          dtWebhook.setAttribute('required', 'required');
        } else {
          dtWebhook.removeAttribute('required');
        }
      } else {
        dtFields.style.opacity = '0.5';
        dtWebhook.removeAttribute('required');
      }
    };
    dtCheck.addEventListener('change', updateDingtalkFields);
    updateDingtalkFields();
  }
  
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
          ${field("用户名输入框定位规则", "username_selector", profile?.username_selector || authSelectorDefaults.username)}
          ${field("密码输入框定位规则", "password_selector", profile?.password_selector || authSelectorDefaults.password)}
          ${field("登录按钮定位规则", "submit_selector", profile?.submit_selector || authSelectorDefaults.submit)}
          ${field("登录成功定位规则", "success_selector", profile?.success_selector || "", "text", false, false, "不确定可留空")}
          ${field("会话状态文件", "storage_state_path", profile?.storage_state_path || "", "text", false, true, "通常留空，由系统自动管理")}
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
  regions.modal.querySelectorAll("form").forEach(bindFormValidation);
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
  if (!validateForm(form)) return;
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
    checkAndScheduleSilentRefresh();
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
    checkAndScheduleSilentRefresh();
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

function getFieldLabel(field) {
  const label = field.closest("label");
  if (!label) return "此项";
  const clone = label.cloneNode(true);
  clone.querySelectorAll("input, textarea, select, button, .form-tip, .field-error").forEach((node) => node.remove());
  return clone.textContent.replace(/\s+/g, " ").trim() || field.getAttribute("placeholder") || "此项";
}

function fieldErrorMessage(field) {
  const label = getFieldLabel(field);
  const value = String(field.value || "").trim();
  if (field.disabled || field.type === "hidden") return "";
  if (field.required && field.type === "checkbox" && !field.checked) return `请勾选${label}`;
  if (field.required && !value) return `请填写${label}`;
  if (value && field.type === "email" && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) return `${label}格式不正确`;
  if (value && field.type === "url") {
    try {
      new URL(value);
    } catch (_) {
      return `${label}格式不正确`;
    }
  }
  if (value && field.type === "number") {
    const numberValue = Number(value);
    const min = field.getAttribute("min");
    const max = field.getAttribute("max");
    if (Number.isNaN(numberValue)) return `${label}必须是数字`;
    if (min !== null && numberValue < Number(min)) return `${label}不能小于 ${min}`;
    if (max !== null && numberValue > Number(max)) return `${label}不能大于 ${max}`;
  }
  return "";
}

function clearFieldError(field) {
  field.classList.remove("field-invalid");
  field.removeAttribute("aria-invalid");
  const label = field.closest("label");
  const oldError = label ? label.querySelector(".field-error") : null;
  if (oldError) oldError.remove();
}

function showFieldError(field, message) {
  clearFieldError(field);
  field.classList.add("field-invalid");
  field.setAttribute("aria-invalid", "true");
  const label = field.closest("label");
  if (!label) return;
  const error = document.createElement("div");
  error.className = "field-error";
  error.textContent = message;
  label.appendChild(error);
}

function bindFormValidation(form) {
  if (!form || form.dataset.validationBound === "1") return;
  form.dataset.validationBound = "1";
  form.setAttribute("novalidate", "novalidate");
  form.querySelectorAll("input, textarea, select").forEach((field) => {
    field.addEventListener("input", () => clearFieldError(field));
    field.addEventListener("change", () => clearFieldError(field));
  });
}

function validateForm(form) {
  bindFormValidation(form);
  let firstInvalid = null;
  form.querySelectorAll("input, textarea, select").forEach((field) => {
    clearFieldError(field);
    const message = fieldErrorMessage(field);
    if (message) {
      showFieldError(field, message);
      if (!firstInvalid) firstInvalid = field;
    }
  });
  if (firstInvalid) {
    showWarning("请先完善表单中的必填信息");
    firstInvalid.scrollIntoView({ block: "center", behavior: "smooth" });
    try {
      firstInvalid.focus({ preventScroll: true });
    } catch (_) {
      firstInvalid.focus();
    }
    return false;
  }
  return true;
}

function securityFormPayload(form) {
  const ttlInput = form.querySelector('[name="session_ttl_minutes"]');
  return {
    session_ttl_minutes: ttlInput ? ttlInput.value : "30",
    mfa_enabled: asBool(state.storage.security?.mfa_enabled),
    mfa_code: ""
  };
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
  const rendered = [`<option value="">请选择</option>`].concat(options.map(([optionValue, text]) => {
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
      <span class="loading-line short"></span>
      <strong class="loading-line"></strong>
    </div>
  `).join("");
}

function loadingTable(rows) {
  return `
    <div class="skeleton-table">
      ${Array.from({ length: rows }, () => `<div class="skeleton-row"><span></span><span></span><span></span></div>`).join("")}
    </div>
  `;
}

function renderError(region, error) {
  region.innerHTML = `<div class="empty">加载失败：${escapeHtml(error.message || "请稍后重试")}</div>`;
}

function renderGlobalError(error) {
  [regions.jobs, regions.recentRuns, regions.runs, regions.auth, regions.mail, regions.periodic].forEach((region) => renderError(region, error));
  regions.metrics.innerHTML = "";
  showError(error.message || "数据加载失败");
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
  const iconName = {
    success: "circle-check",
    error: "circle-alert",
    warning: "triangle-alert",
    info: "info"
  }[type] || "info";

  toast.innerHTML = `
    <div class="toast-icon-wrapper">${icon(iconName)}</div>
    <div class="toast-content">
      <div class="toast-title">${escapeHtml(realTitle)}</div>
      ${realDesc ? `<div class="toast-desc">${escapeHtml(realDesc)}</div>` : ""}
    </div>
    <button class="toast-close-btn" type="button" aria-label="关闭">${icon("x")}</button>
    <div class="toast-progress">
      <div class="toast-progress-bar" style="transform: scaleX(1);"></div>
    </div>
  `;

  container.appendChild(toast);
  renderIcons(toast);

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
    const confirmIcon = danger ? "triangle-alert" : "circle-help";

    confirmRoot.innerHTML = `
      <section class="confirm-panel ${danger ? "confirm-danger" : ""}" role="dialog" aria-modal="true">
        <div class="confirm-head">
          <div class="confirm-icon">${icon(confirmIcon)}</div>
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
    renderIcons(confirmRoot);

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

    const swaggerData = await apiGet("/swagger-settings");
    state.storage.swagger = swaggerData || { enabled: false };

    const securityData = await apiGet("/security-settings");
    state.storage.security = securityData || {};
    
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
  const swagger = state.storage.swagger || { enabled: false };
  const swaggerEnabled = asBool(swagger.enabled);
  const security = state.storage.security || {};

  let scheduleCfg = {};
  if (config.cleanup_schedule_config) {
    try {
      scheduleCfg = JSON.parse(config.cleanup_schedule_config);
    } catch (e) {
      console.error("存储清理时间配置解析失败:", e);
    }
  }
  const scheduleTime = scheduleCfg.time || "02:30";
  const dayOfWeek = scheduleCfg.day_of_week || "1";
  const dayOfMonth = scheduleCfg.day_of_month || "1";

  const storageMetricsHTML = `
    <div class="metrics-grid">
      <div>
        <span>数据目录占用</span>
        <strong>${formatBytes(usage.total_bytes || 0)}</strong>
      </div>
      <div>
        <span>截图占用</span>
        <strong>${formatBytes(usage.screenshots_bytes || 0)}</strong>
      </div>
      <div>
        <span>Word 报告占用</span>
        <strong>${formatBytes(usage.reports_bytes || 0)}</strong>
      </div>
      <div>
        <span>数据库占用</span>
        <strong>${formatBytes(usage.sqlite_db_bytes || 0)}</strong>
      </div>
    </div>
  `;

  const swaggerControlHTML = renderSwaggerControl(swaggerEnabled);
  const securityControlHTML = renderSecurityControl(security);

  const detailsHTML = `
    <div class="card-grid storage-grid">
      <div class="panel">
        <div class="panel-header">
          <h3>空间明细</h3>
        </div>
        <table>
          <tbody>
            <tr><td>周期报告 ZIP 占用</td><td><strong>${formatBytes(usage.periodic_reports_bytes || 0)}</strong></td></tr>
            <tr><td>日志文件占用</td><td><strong>${formatBytes(usage.logs_bytes || 0)}</strong></td></tr>
            <tr><td>浏览器会话文件占用</td><td><strong>${formatBytes(usage.browser_state_bytes || 0)}</strong></td></tr>
          </tbody>
        </table>

        <div class="panel-header section-gap">
          <h3>清理预估</h3>
        </div>
        <div class="surface-soft storage-action-box">
          <p class="muted">预计可清理 <strong class="ok-text" id="est-bytes-text">${formatBytes(usage.estimated_cleanup_bytes || 0)}</strong>，共 <strong id="est-files-text">${usage.estimated_cleanup_files || 0}</strong> 个文件。</p>
          <div class="action-row">
            <button class="button" type="button" data-action="estimate-storage">${icon("calculator")}重新估算</button>
            <button class="button danger" type="button" data-action="run-storage-cleanup">${icon("trash-2")}立即清理</button>
          </div>
        </div>
        ${swaggerControlHTML}
        ${securityControlHTML}
      </div>

      <div class="panel">
        <div class="panel-header">
          <h3>自动清理设置</h3>
        </div>
        <form class="form-grid" data-form="storage-settings">
          <label class="check span-2">
            <input type="checkbox" name="enabled" ${asBool(config.enabled) ? "checked" : ""}>
            启用自动清理
          </label>

          <label>
            清理频率
            <select name="cleanup_schedule_mode">
              <option value="daily" ${config.cleanup_schedule_mode === "daily" ? "selected" : ""}>每天</option>
              <option value="weekly" ${config.cleanup_schedule_mode === "weekly" ? "selected" : ""}>每周</option>
              <option value="monthly" ${config.cleanup_schedule_mode === "monthly" ? "selected" : ""}>每月</option>
            </select>
          </label>

          <label>
            清理时间
            <input type="time" name="schedule_time" value="${escapeAttr(scheduleTime)}" required>
          </label>

          <label id="storage-dow-label">
            每周几
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
            每月几号
            <input type="number" name="schedule_day_of_month" min="1" max="31" value="${escapeAttr(dayOfMonth)}">
          </label>

          <label class="check span-2">
            <input type="checkbox" name="allow_manual_cleanup" ${asBool(config.allow_manual_cleanup) ? "checked" : ""}>
            允许在页面手动清理
          </label>

          <div class="form-section-title span-2">保留时间</div>

          <label>
            已发送周期报告
            <input type="number" name="periodic_sent_retention_days" min="1" value="${config.periodic_sent_retention_days || 3}" required>
          </label>

          <label>
            发送失败周期报告
            <input type="number" name="periodic_failed_retention_days" min="1" value="${config.periodic_failed_retention_days || 30}" required>
          </label>

          <label>
            截图文件
            <input type="number" name="screenshot_retention_days" min="1" value="${config.screenshot_retention_days || 7}" required>
          </label>

          <label>
            普通 Word 报告
            <input type="number" name="report_retention_days" min="1" value="${config.report_retention_days || 30}" required>
          </label>

          <label>
            日志文件
            <input type="number" name="log_retention_days" min="1" value="${config.log_retention_days || 14}" required>
          </label>

          <label>
            运检记录
            <input type="number" name="run_record_retention_days" min="1" value="${config.run_record_retention_days || 90}" required>
          </label>

          <label class="check span-2">
            <input type="checkbox" name="browser_state_cleanup_enabled" ${asBool(config.browser_state_cleanup_enabled) ? "checked" : ""}>
            清理过期浏览器会话文件
          </label>

          <label id="storage-browser-retention-label">
            浏览器会话保留天数
            <input type="number" name="browser_state_retention_days" min="1" value="${config.browser_state_retention_days || 30}" required>
          </label>

          <label>
            保护最近数据
            <input type="number" name="protect_recent_days" min="1" value="${config.protect_recent_days || 3}" required>
            <span class="form-tip">最近几天的数据不会被自动清理</span>
          </label>

          <div class="form-actions span-2">
            <button class="button primary" type="submit">${icon("save")}保存设置</button>
          </div>
        </form>
      </div>
    </div>
  `;

  const historyHTML = `
    <div class="panel storage-history">
      <div class="panel-header">
        <h2>清理记录</h2>
      </div>
      ${cleanupRuns.length ? `
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>清理方式</th>
              <th>状态</th>
              <th>开始时间</th>
              <th>删除文件</th>
              <th>释放空间</th>
              <th>删除记录数</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            ${cleanupRuns.map((run) => `
              <tr>
                <td>#${run.id}</td>
                <td><code>${run.mode === "auto" ? "自动" : run.mode === "manual" ? "手动" : "未知"}</code></td>
                <td>${statusBadge(run.status, run.status === "success" ? "成功" : run.status === "partial_success" ? "部分成功" : "失败")}</td>
                <td>${escapeHtml(shortDate(run.started_at))}</td>
                <td>${numberText(run.deleted_files_count)} 个</td>
                <td><strong>${formatBytes(run.deleted_bytes)}</strong></td>
                <td>${numberText(run.deleted_run_records_count)} 条</td>
                <td class="table-note" title="${escapeAttr(run.error_summary || "")}">
                  ${run.error_summary ? `<span class="bad-text">${escapeHtml(run.error_summary)}</span>` : '<span class="ok-text">执行正常</span>'}
                </td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      ` : `<div class="empty">暂无清理记录。</div>`}
    </div>
  `;

  regions.storage.innerHTML = storageMetricsHTML + detailsHTML + historyHTML;
  const form = regions.storage.querySelector('[data-form="storage-settings"]');
  bindStorageFormEvents(form);
  bindSecurityForm(regions.storage.querySelector('[data-form="security-settings"]'));
  bindMfaToggle(regions.storage.querySelector('.security-mfa-box input[name="mfa_enabled"]'));
  bindDynamicActions(regions.storage);
  renderQrCodes(regions.storage);
}

function bindStorageFormEvents(form) {
  if (!form) return;
  bindFormValidation(form);
  
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
    if (!validateForm(form)) return;
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
    showToast("正在估算可清理空间...", "info");
    const result = await apiJson("/storage/estimate", { method: "POST" });
    if (result.ok && result.result) {
      const res = result.result;
      const bytesEl = document.getElementById("est-bytes-text");
      const filesEl = document.getElementById("est-files-text");
      if (bytesEl) bytesEl.innerText = formatBytes(res.deleted_bytes);
      if (filesEl) filesEl.innerText = String(res.deleted_files_count);
      showSuccess(`预计可清理 ${formatBytes(res.deleted_bytes)}，共 ${res.deleted_files_count} 个文件`);
    } else {
      showError("估算失败，请稍后重试");
    }
  } catch (error) {
    showError(error.message || "估算失败");
  } finally {
    setButtonLoading(btn, false);
  }
}

async function runStorageCleanup(btn) {
  const confirmed = await showConfirm({
    title: "确认清理数据？",
    message: "系统会按照保留时间删除过期截图、报告、日志和会话文件。此操作不可撤销。",
    confirmText: "立即清理",
    cancelText: "取消",
    danger: true
  });
  if (!confirmed) return;

  setButtonLoading(btn, true);
  try {
    showToast("正在清理过期数据...", "info");
    const result = await apiJson("/storage/cleanup", { method: "POST" });
    if (result.ok && result.result) {
      const res = result.result;
      showSuccess(`清理完成，删除 ${res.deleted_files_count} 个文件，释放 ${formatBytes(res.deleted_bytes)}`);
      await loadStorage(false);
    } else {
      showError("清理失败，请稍后重试");
    }
  } catch (error) {
    showError(error.message || "清理失败，请检查后端日志");
  } finally {
    setButtonLoading(btn, false);
  }
}

function renderSwaggerControl(enabled) {
  return `
    <div class="panel-header section-gap">
      <h3>接口文档</h3>
    </div>
    <div class="surface-soft storage-action-box">
      <div class="row-between swagger-row">
        <div>
          <div class="strong-title">接口文档访问</div>
          <p class="muted">默认关闭，需要登录后手动开启。</p>
        </div>
        <span class="badge ${enabled ? "success" : "muted"}">${enabled ? "已开启" : "已关闭"}</span>
      </div>
      <div class="action-row">
        ${enabled ? `
          <a class="button primary" href="/docs" target="_blank" rel="noopener">${icon("external-link")}打开文档</a>
          <button class="button" type="button" data-action="disable-swagger">${icon("lock")}关闭</button>
        ` : `
          <button class="button primary" type="button" data-action="enable-swagger">${icon("unlock")}开启文档</button>
        `}
      </div>
    </div>
  `;
}

function renderSecurityControl(security) {
  const enabled = asBool(security.mfa_enabled);
  const configured = asBool(security.mfa_configured);
  const ttl = Number(security.session_ttl_minutes || 30);
  return `
    <div class="panel-header section-gap">
      <h3>安全设置</h3>
    </div>
    <form class="surface-soft storage-action-box security-settings-box security-session-box" data-form="security-settings">
      <label>
        登录有效期
        <select name="session_ttl_minutes">
          <option value="30" ${ttl === 30 ? "selected" : ""}>30 分钟</option>
          <option value="120" ${ttl === 120 ? "selected" : ""}>2 小时</option>
          <option value="480" ${ttl === 480 ? "selected" : ""}>8 小时</option>
          <option value="1440" ${ttl === 1440 ? "selected" : ""}>1 天</option>
          <option value="10080" ${ttl === 10080 ? "selected" : ""}>7 天</option>
        </select>
      </label>
      <div class="action-row">
        <button class="button primary" type="submit">${icon("save")}保存安全设置</button>
      </div>
    </form>
    <div class="surface-soft storage-action-box security-settings-box security-mfa-box">
      <label class="check mfa-toggle">
        <input type="checkbox" name="mfa_enabled" ${enabled ? "checked" : ""}>
        开启 MFA 认证
      </label>
    </div>
  `;
}

async function updateSwaggerSettings(enabled, btn) {
  setButtonLoading(btn, true);
  try {
    const result = await apiJson("/swagger-settings", {
      method: "PUT",
      body: { enabled }
    });
    state.storage.swagger = { enabled: Boolean(result.enabled) };
    renderStorage();
    showSuccess(enabled ? "接口文档已开启" : "接口文档已关闭");
  } catch (error) {
    showError(error.message || "接口文档状态更新失败");
  } finally {
    setButtonLoading(btn, false);
  }
}

async function generateMfaSecret(btn) {
  await openMfaSetupModal(btn);
}

async function openMfaSetupModal(toggleInput = null) {
  const modalRoot = regions.modal;
  if (!modalRoot) return;
  if (toggleInput) toggleInput.disabled = true;

  try {
    const result = await apiJson("/security-settings/mfa-secret", { method: "POST" });
    const secret = result.mfa_secret || result.secret || "";
    const uri = result.mfa_otpauth_url || result.qr_uri || "";
    state.storage.security = {
      ...state.storage.security,
      mfa_enabled: false,
      mfa_configured: true,
      mfa_secret: secret,
      mfa_otpauth_url: uri
    };

    modalRoot.hidden = false;
    modalRoot.innerHTML = `
      <section class="modal-panel mfa-modal-panel" role="dialog" aria-modal="true">
        <div class="modal-title">
          <h2>绑定 MFA 认证</h2>
          <button class="modal-close-btn" type="button" data-action="close-mfa-modal" aria-label="关闭">×</button>
        </div>
        <form class="mfa-bind-form" data-form="mfa-bind">
          <div class="mfa-setup-card mfa-setup-card-modal">
            <div class="mfa-qr-box">
              <canvas data-qr-value="${escapeAttr(uri)}" aria-label="MFA 绑定二维码"></canvas>
            </div>
            <div class="mfa-setup-copy">
              <div class="strong-title">使用认证器 App 扫描二维码</div>
              <p class="muted">扫描后输入 App 中显示的 6 位验证码，验证成功后 MFA 会立即开启。</p>
              <div class="mfa-secret-row">
                <span>手动密钥</span>
                <code>${escapeHtml(secret)}</code>
              </div>
            </div>
          </div>
          <label>
            MFA 验证码
            <input name="mfa_code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="输入 6 位验证码" required>
          </label>
          <div class="form-actions">
            <button class="button" type="button" data-action="close-mfa-modal">取消</button>
            <button class="button primary" type="submit">${icon("shield-check")}验证并开启</button>
          </div>
        </form>
      </section>
    `;
    renderIcons(modalRoot);
    renderQrCodes(modalRoot);
    bindMfaSetupModal(modalRoot, toggleInput);
    const codeInput = modalRoot.querySelector('[name="mfa_code"]');
    if (codeInput) codeInput.focus();
  } catch (error) {
    showError(error.message || "生成 MFA 绑定二维码失败");
    if (toggleInput) {
      toggleInput.checked = false;
      toggleInput.disabled = false;
    }
  }
}

function closeMfaSetupModal(toggleInput = null) {
  if (regions.modal) {
    regions.modal.hidden = true;
    regions.modal.innerHTML = "";
  }
  window.removeEventListener("keydown", mfaModalKeydownHandler);
  if (toggleInput) {
    toggleInput.checked = asBool(state.storage.security?.mfa_enabled);
    toggleInput.disabled = false;
  }
}

function mfaModalKeydownHandler(event) {
  if (event.key === "Escape") {
    closeMfaSetupModal(document.querySelector('.security-mfa-box input[name="mfa_enabled"]'));
  }
}

function bindMfaSetupModal(root, toggleInput = null) {
  const form = root.querySelector('[data-form="mfa-bind"]');
  window.addEventListener("keydown", mfaModalKeydownHandler);
  root.querySelectorAll('[data-action="close-mfa-modal"]').forEach((button) => {
    button.addEventListener("click", () => closeMfaSetupModal(toggleInput));
  });
  if (!form) return;
  bindFormValidation(form);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!validateForm(form)) return;
    const submitButton = form.querySelector('button[type="submit"]');
    const codeInput = form.querySelector('[name="mfa_code"]');
    const payload = {
      session_ttl_minutes: state.storage.security?.session_ttl_minutes || 30,
      mfa_enabled: true,
      mfa_code: codeInput ? codeInput.value.trim() : ""
    };
    if (!payload.mfa_code) {
      showWarning("请输入认证器 App 中的 6 位 MFA 验证码。");
      if (codeInput) codeInput.focus();
      return;
    }
    setButtonLoading(submitButton, true);
    try {
      const result = await apiJson("/security-settings", { method: "PUT", body: payload });
      state.storage.security = result || {};
      closeMfaSetupModal();
      renderStorage();
      showSuccess("MFA 认证已开启");
    } catch (error) {
      showError(error.message || "MFA 认证开启失败，请输入最新的 6 位验证码。");
      if (codeInput) {
        codeInput.value = "";
        codeInput.focus();
      }
    } finally {
      setButtonLoading(submitButton, false);
      if (toggleInput) toggleInput.disabled = false;
    }
  });
}

function bindMfaToggle(mfaToggle) {
  if (!mfaToggle) return;
  mfaToggle.addEventListener("change", () => {
    if (mfaToggle.checked) {
      openMfaSetupModal(mfaToggle);
    } else {
      confirmDisableMfa(mfaToggle);
    }
  });
}

async function confirmDisableMfa(toggleInput = null) {
  if (toggleInput) toggleInput.disabled = true;
  const confirmed = await showConfirm({
    title: "关闭 MFA 认证？",
    message: "关闭后，登录时将不再要求输入 6 位动态验证码。请确认这是你想要的操作。",
    confirmText: "关闭 MFA",
    cancelText: "取消",
    danger: true
  });
  if (!confirmed) {
    if (toggleInput) {
      toggleInput.checked = true;
      toggleInput.disabled = false;
    }
    return;
  }

  try {
    const result = await apiJson("/security-settings", {
      method: "PUT",
      body: {
        session_ttl_minutes: state.storage.security?.session_ttl_minutes || 30,
        mfa_enabled: false,
        mfa_code: ""
      }
    });
    state.storage.security = result || {};
    renderStorage();
    showSuccess("MFA 认证已关闭");
  } catch (error) {
    showError(error.message || "MFA 认证关闭失败");
    if (toggleInput) {
      toggleInput.checked = true;
      toggleInput.disabled = false;
    }
  }
}

function bindSecurityForm(form) {
  if (!form) return;
  bindFormValidation(form);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!validateForm(form)) return;
    const submitButton = form.querySelector('button[type="submit"]');
    setButtonLoading(submitButton, true);
    try {
      const payload = securityFormPayload(form);
      const result = await apiJson("/security-settings", { method: "PUT", body: payload });
      state.storage.security = result || {};
      renderStorage();
      showSuccess("安全设置已保存");
    } catch (error) {
      showError(error.message || "安全设置保存失败");
    } finally {
      setButtonLoading(submitButton, false);
    }
  });
}

function formatBytes(bytes) {
  if (bytes === undefined || bytes === null || isNaN(bytes)) return "-";
  if (bytes === 0) return "0 Bytes";
  const k = 1024;
  const sizes = ["Bytes", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

