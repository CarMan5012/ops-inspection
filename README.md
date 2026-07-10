# 自动化巡检报告系统

轻量级 Web 配置后台，用于配置 Grafana、Kibana/Elastic、普通网页截图任务，定时使用 Playwright/Chromium 截图，生成 Word 巡检报告，并通过 SMTP 邮件推送。

## 功能

- Web 页面配置巡检任务
- 自定义截图 URL、CSS 选择器、等待规则、浏览器尺寸
- 支持 Grafana、Kibana、普通网页截图
- 手动测试单张截图并预览
- 手动执行巡检任务
- APScheduler 定时执行
- 生成 `.docx` 报告
- SMTP 邮件推送
- 历史执行记录、截图和报告下载

> 第一版建议单实例运行。SQLite、APScheduler 和本地浏览器状态文件都适合单容器部署；需要多实例时，应改为外部数据库、队列和独立 worker。

## 本地运行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

访问：

```text
http://localhost:8000
```

## Docker 运行

```bash
cp .env.example .env
# 编辑 .env 中的管理员账号密码
docker build -t ops-inspection:latest .
bash docker-run.sh
```

或者使用 Docker Compose：

```bash
cp .env.example .env
# 编辑 .env 中的管理员账号密码
# 首次启动前请按 secrets/README.md 生成 secrets/app_secret_key 密钥文件。
docker compose up -d --build
```

访问：

```text
巡检系统: http://localhost:8000
Grafana: http://localhost:3000
```

## 本地 Grafana 测试

Docker Compose 会同时启动巡检系统和一个本地 Grafana，暂时不需要本地搭建 Kibana。

- Grafana 镜像：`.env` 中的 `GRAFANA_IMAGE`，默认 `grafana/grafana-oss:11.4.0`
- 默认账号：`.env` 中的 `GRAFANA_USER` / `GRAFANA_PASS`
- 默认测试账号：`admin` / `admin`
- 预置数据源：`Local TestData`
- 预置 Dashboard：`Local Grafana Inspection Test`

启动：

```powershell
Copy-Item .env.example .env
docker compose up -d --build
```

浏览器访问 Grafana：

```text
http://localhost:3000/d/local-inspection-test/local-grafana-inspection-test
```

在巡检系统里测试 Grafana 截图：

1. 访问 `http://localhost:8000`，用 `.env` 中的 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 登录。
2. 在 `Auth` 中编辑 `Grafana 用户名密码`。
3. `Login URL` 填 `http://localhost:3000/login`。
4. 用户名和密码直接填写 `.env` 中的 `GRAFANA_USER` / `GRAFANA_PASS`。
5. `Success selector` 可留空，或按需填写 `.react-grid-layout`。
6. 在 `Jobs` 里新建或打开一个任务，添加截图项。
7. 截图项 `Type` 选 `grafana`，`URL` 填：

```text
http://localhost:3000/d/local-inspection-test/local-grafana-inspection-test?orgId=1&from=now-1h&to=now&kiosk
```

8. `Capture mode` 建议先选 `viewport` 或 `full_page`，`Wait selector` 填 `.react-grid-layout`。
9. 保存后点击截图项的 `Test`，到 `Runs` 查看截图结果。

> 本地测试时直接填写浏览器可访问的完整地址即可；生产环境部署后，在 Web 页面改成生产 Grafana 地址。

## 环境变量

```env
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me

GRAFANA_USER=admin
GRAFANA_PASS=admin
GRAFANA_IMAGE=grafana/grafana-oss:11.4.0
KIBANA_USER=readonly-user
KIBANA_PASS=readonly-password
SMTP_PASSWORD=mail-password
```

容器化部署时不要把主密钥写进 `.env`。请按 `secrets/README.md` 生成 `secrets/app_secret_key`，`docker-compose.yml` 会把它以 Docker Secret 方式挂载到 `/run/secrets/app_secret_key`。这个文件必须单独备份，丢失后数据库中已加密的邮箱密码、MFA 密钥、钉钉密钥等将无法解密。

Web 页面会显示当前配置，后续也可以直接在页面修改。

如果 `docker compose up -d --build` 拉取 Grafana 镜像失败，请先在 Docker Desktop 中配置可用代理，或把 `.env` 里的 `GRAFANA_IMAGE` 改成你内网镜像仓库中的同版本 Grafana 镜像。

## 使用建议

1. 先在“Auth”里配置 Grafana/Kibana 登录方式。用户名、密码只填环境变量名。
2. 在“Jobs”里创建巡检任务，设置 Cron、浏览器尺寸和是否发送邮件。
3. 在任务详情里添加截图项。Grafana 可配置等待选择器 `.react-grid-layout`；Kibana 可配置 `[data-test-subj="dashboardViewport"]`。
4. 对每个截图项先点 `Test`，确认截图可预览后再启用定时任务。
5. 邮件配置完成后，在任务里开启 `Send mail after report`。

## 真实浏览器窗口截图

本系统默认开启 **真实浏览器窗口截图**，该功能通过容器内 `Xvfb` (虚拟显示) + Playwright 有头模式启动 Chromium，并在页面渲染完成后使用 `mss` 截取真实的浏览器物理窗口（截图中包含真实地址栏、标签栏及窗口边框）。

### 部署与配置要求
* **容器运行**：容器启动时会自动拉起后台 Xvfb 服务并配置 `DISPLAY=:99` 环境变量。请确保 Docker 运行在 seccomp 限制宽松或默认环境中，以使 Chromium 顺利启动。
* **物理窗口限制**：该模式仅支持 **视口截图 (viewport)**。如果配置中勾选了该功能，即使截图项获取模式选择了“整页长截图”，系统在后台也会自动将其调整为“视口截图”执行。
* **如何切回普通模式**：
  在截图项配置页面中，您可以随时**取消勾选**“真实浏览器窗口截图”选项。取消后将切换为普通 Playwright 无头页面截图，该模式下支持完整的“整页长截图” (`full_page`) 及局部特定元素截图 (`selector`)，但截图上不再包含浏览器真实地址栏。

## 目录

```text
data/
  app.db
  screenshots/
  reports/
  logs/
  browser-state/
```

## 前后端分离入口

当前登录后的 `/` 已切换为静态前端应用，资源位于 `app/static/frontend/`，页面通过 `/api/*` JSON 接口读取和更新数据。

- `/api/dashboard`：总览数据、任务、最近运行、认证和邮件配置摘要
- `/api/jobs`、`/api/jobs/{job_id}`：任务列表、创建、更新、删除
- `/api/jobs/{job_id}/run`：触发巡检任务
- `/api/runs`、`/api/runs/{run_id}`：运行记录与截图结果
- `/api/auth-profiles`、`/api/mail-profiles`：认证与邮件配置
- `/api/periodic-reports`：周期报告配置和历史包
