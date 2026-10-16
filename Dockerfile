FROM node:24-bookworm-slim AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    APP_DATA_DIR=/app/data \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    TZ=Asia/Shanghai \
    DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    XVFB_WIDTH=4000 \
    XVFB_HEIGHT=2250 \
    XVFB_DEPTH=24 \
    LANG=zh_CN.UTF-8 \
    LANGUAGE=zh_CN:zh \
    LC_ALL=zh_CN.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk \
    fonts-wqy-zenhei \
    tzdata \
    xvfb \
    locales \
    build-essential \
    libsqlcipher-dev \
    sqlcipher \
    python3-dev \
    && sed -i -e 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen \
    && ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime \
    && echo ${TZ} > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .


RUN pip install --no-cache-dir -r requirements.txt


RUN playwright install-deps chrome \
    && apt-get update && apt-get install -y --no-install-recommends curl \
    && curl -fsSL --retry 5 -o /tmp/google-chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y /tmp/google-chrome.deb \
    && rm -f /tmp/google-chrome.deb \
    && mkdir -p /etc/opt/chrome/policies/managed /etc/chromium/policies/managed \
    && echo '{"TranslateEnabled": false, "PasswordManagerEnabled": false}' > /etc/opt/chrome/policies/managed/managed_policies.json \
    && cp /etc/opt/chrome/policies/managed/managed_policies.json /etc/chromium/policies/managed/managed_policies.json

RUN rm -f /etc/apt/sources.list.d/google-chrome.list \
    && sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends openbox x11-utils \
    && rm -rf /var/lib/apt/lists/*

COPY app ./app
COPY --from=frontend-build /build/app/static/vue ./app/static/vue
RUN mkdir -p /app/data/screenshots /app/data/reports /app/data/templates /app/data/taskbar-templates /app/data/logs /app/data/browser-state

EXPOSE 8000

CMD ["sh", "-c", "rm -f /tmp/.X99-lock && Xvfb ${DISPLAY:-:99} -screen 0 ${XVFB_WIDTH:-4000}x${XVFB_HEIGHT:-2250}x${XVFB_DEPTH:-24} -ac +extension GLX +render -noreset & until xdpyinfo -display ${DISPLAY:-:99} >/dev/null 2>&1; do sleep 0.2; done; openbox --sm-disable & until xprop -root _NET_SUPPORTING_WM_CHECK 2>/dev/null | grep -q 'window id'; do sleep 0.2; done; exec uvicorn app.main:app --host ${APP_HOST} --port ${APP_PORT}"]
