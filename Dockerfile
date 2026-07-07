FROM python:3.11-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    APP_DATA_DIR=/app/data \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    TZ=Asia/Shanghai \
    DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    XVFB_WIDTH=3840 \
    XVFB_HEIGHT=2160 \
    XVFB_DEPTH=24

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk \
    fonts-wqy-zenhei \
    tzdata \
    xvfb \
    && ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime \
    && echo ${TZ} > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .


RUN pip install --no-cache-dir -r requirements.txt


RUN playwright install --with-deps chromium

COPY app ./app
COPY 服务器巡检-yyyy-mm-dd.docx .

RUN mkdir -p /app/data/screenshots /app/data/reports /app/data/logs /app/data/browser-state

EXPOSE 8000

CMD ["sh", "-c", "rm -f /tmp/.X99-lock && Xvfb ${DISPLAY:-:99} -screen 0 ${XVFB_WIDTH:-3840}x${XVFB_HEIGHT:-2160}x${XVFB_DEPTH:-24} -ac +extension GLX +render -noreset & uvicorn app.main:app --host ${APP_HOST} --port ${APP_PORT}"]
