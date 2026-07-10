# 自动化巡检报告系统

本系统是一个轻量级的 **Web 运维巡检配置后台**。它支持对 Grafana、Kibana/Elastic 及任意普通网页进行自动化截图，基于配置的 Cron 表达式定时运行，自动生成 `.docx` 格式的 Word 巡检报告，并通过 SMTP 邮件进行自动推送。

---

## 1. 核心业务流程与架构

系统主要包含 **任务调度、自动化登录与截图、图像后处理、报告生成、邮件推送** 五个核心模块。以下是系统在触发一次巡检任务时的底层逻辑流程图：

```mermaid
graph TD
    A([定时任务 / 手动触发]) --> B[初始化浏览器环境]
    B --> C{是否关联登录认证?}
    C -- 是 --> D{是否存在 Session 缓存?}
    D -- 否 --> E[Playwright 模拟打开登录页面]
    E --> F[自动填充账号密码并点击登录]
    F --> G[加密保存 Session 状态至本地]
    G --> H[载入目标巡检网页]
    D -- 是 --> I[解密并载入已有 Session 状态]
    I --> H
    C -- 否 --> H
    H --> J[等待网络空闲 & 元素渲染完成]
    H -. 登录态失效重定向 .-> F
    J --> K{是否开启真实浏览器窗口截图?}
    K -- 是 --> L[通过 Xvfb 虚拟显示 + mss 物理截图]
    K -- 否 --> M[Playwright 网页级截图 (视口/整页)]
    L --> N[Pillow 拼装任务栏底栏 & 绘制真实时钟]
    M --> O[Pillow 图像后处理: 添加斜体平铺水印]
    N --> P[生成并排版 .docx Word 报告]
    O --> P
    P --> Q{是否开启邮件推送?}
    Q -- 是 --> R[通过 SMTP 发送报告邮件]
    Q -- 否 --> S([巡检任务结束])
    R --> S
```

---

## 2. 技术栈

* **Web 后端**：基于 **FastAPI** + **Uvicorn**，提供高性能的异步接口和静态资源托管。
* **定时调度**：基于 **APScheduler**，在后台维护内存定时任务队列，支持秒/分/时/日级别的 Cron 定时触发。
* **安全数据库**：基于 **SQLite**，通过 **SQLCipher** 拓展进行物理加密，确保即使数据库文件被拷走，敏感配置数据也无法被读取。
* **网页自动化**：基于 **Playwright (Chromium)**，通过有头或无头模式加载目标系统，支持全自动化登录表单填充与状态缓存。
* **物理窗口截图**：在容器内启动 **Xvfb** (虚拟帧缓冲) 构建图形环境，并利用 Python 的 **mss** 库截取真实的 Chromium 物理桌面窗口，还原浏览器地址栏和标签。
* **图像后处理**：基于 **Pillow (PIL)**，进行平铺防伪水印叠加，并模拟 Windows 任务栏时钟，按照截图时间动态渲染右下角时间。
* **报告排版**：基于 **python-docx**，自动读取 `.docx` 模板文件，自动进行图片自适应缩放并排版生成报告。
* **前端展示**：单页面应用 (SPA)，使用原生 Vanilla JavaScript + TailwindCSS (预编译) 进行敏捷交互。

---

## 3. 本地开发调试

### 3.1 环境依赖
您需要安装 Python 3.11+ 并在本地安装 Chromium 浏览器依赖。

### 3.2 运行步骤（以 Windows PowerShell 为例）
```powershell
# 1. 创建并激活虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 安装依赖包
pip install -r requirements.txt

# 3. 安装 Playwright 底层 Chromium 浏览器
playwright install chromium

# 4. 运行后台服务 (本地调试默认会在数据目录自动创建 app-secret.key)
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
访问系统：`http://localhost:8000/ops`

---

## 4. 容器化部署

### 4.1 使用一键运行脚本 (单容器模式)
系统提供了 [docker-run.sh](docker-run.sh) 脚本，可快速在 Linux 生产服务器上部署。

```bash
# 1. 复制配置文件模板并根据需要更改
cp .env.example .env

# 2. 构建 Docker 镜像
docker build -t ops-inspection:latest .

# 3. 运行部署脚本（自动停止旧容器，启动新容器并做安全加固）
bash docker-run.sh
```

### 4.2 使用 Docker Compose 部署 (多容器模式)
如果您需要启动系统自带的本地测试 Grafana，可以使用 Docker Compose。

```bash
# 1. 生成加密主密钥（建议长度 48 字节的随机 Token）
mkdir -p secrets
python -c "import secrets; print(secrets.token_urlsafe(48))" > secrets/app_secret_key
chmod 600 secrets/app_secret_key

# 2. 启动 Compose
docker compose up -d --build
```
访问系统：`http://localhost:8000/ops`

---

## 5. 安全与密钥设计

为了保障生产环境下各项凭证（发信密码、目标系统密码）的安全，系统设计了双重密钥体系：

1. **数据库物理加密 (`SQLCIPHER_DB_KEY`)**：
   * 在启动脚本 [docker-run.sh](docker-run.sh) 中设置，用于开启 SQLCipher 对 SQLite 数据库文件在磁盘上的物理级加密，强防库文件物理泄漏。
2. **应用敏感字段加密 (`APP_SECRET_KEY`)**：
   * 系统的主密钥，用于通过 Fernet/AES 算法在应用层对敏感输入（如邮箱 SMTP 授权码、登录凭证、钉钉 Token）进行二次加密。
   * **推荐挂载方案**：将主密钥文件 `secrets/app_secret_key` 单独备份于宿主机，通过只读卷挂载到容器内 `/run/secrets/app_secret_key`。
   * **自动生成方案**：若不挂载，系统会在持久化目录（宿主机 `/data/ops-inspection/data`）下自动生成 `app-secret.key` 文件。只要该目录不丢失，密钥即保持一致。

---

## 6. 特色功能详解

### 6.1 真实浏览器窗口截图 (Xvfb + mss)
* **原理**：很多时候监控截图需要呈现浏览器地址栏以证明其真实有效性。系统会在后台利用虚拟帧缓冲技术拉起一个真实的 Chromium 窗口（有头模式），并通过 Python 直接在屏幕像素级别截取这个浏览器窗口，从而完美保留地址栏、标签页以及窗口边框。
* **配置方式**：在新建截图项时勾选 `real_browser_capture`（真实浏览器窗口截图）选项。
* **限制**：该模式下浏览器渲染仅支持 **视口截图 (viewport)**。如果需要截取整页长图，请取消勾选此项以回退至标准 Playwright 无头页面截图模式。

### 6.2 自动登录与 Session 自愈
* **表单模拟**：配置认证方案后，系统会在第一次加载页面前，自动前往 `Login URL`，定位用户名和密码输入框，填充凭证并提交。
* **状态持久化**：登录成功后，系统会将其 Session (Cookies、LocalStorage 等) 经主密钥加密后序列化到磁盘（`browser-state/`）。后续截图任务会直接载入缓存，省去频繁登录操作，提高运行速度。
* **Session 自愈**：若截图加载中发现被重定向回了登录页，系统会自动判定 Session 已过期，立即物理清除本地 Session 缓存，拉起浏览器重新走一遍登录流程，无需人工干预。

### 6.3 图像后处理 (任务栏 & 水印)
* **动态任务栏**：在截图底部拼接 Windows 任务栏背景，并利用系统字体根据截图时的真实时间，在任务栏右下角自动绘制电子时钟（支持高精度渲染），消除拼接痕迹。
* **斜体水印**：支持自定义文字（包含截图时间、巡检人员姓名等占位符），在整张截图上平铺旋转水印，兼顾安全合规性与防伪需求。

---

## 7. 持久化数据目录结构

所有生成的报告、运行记录均保存在宿主机的映射目录 `/data/ops-inspection/data` 下，目录结构如下：

```text
data/
  ├── app.db               # 加密过的 SQLite 数据库文件
  ├── app-secret.key       # 自动生成的解密主密钥文件（仅在未手动挂载密钥时存在）
  ├── screenshots/         # 存放按运行 ID (run_id) 隔离的巡检截图
  ├── reports/             # 存放生成的 .docx Word 格式巡检报告
  ├── logs/                # 存放巡检执行的系统运行日志
  └── browser-state/       # 存放加密后的浏览器 Session 缓存 JSON 文件
```
