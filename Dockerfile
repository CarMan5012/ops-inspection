# FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

# WORKDIR /app

# ENV PYTHONUNBUFFERED=1 \
#     APP_DATA_DIR=/app/data \
#     APP_HOST=0.0.0.0 \
#     APP_PORT=8000 \
#     TZ=Asia/Shanghai \
#     DEBIAN_FRONTEND=noninteractive

# COPY requirements.txt .
# RUN apt-get update \
#     && apt-get install -y --no-install-recommends fonts-noto-cjk fonts-wqy-zenhei tzdata \
#     && ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime \
#     && echo ${TZ} > /etc/timezone \
#     && rm -rf /var/lib/apt/lists/*
# RUN pip install --no-cache-dir -r requirements.txt

# COPY app ./app
# COPY 服务器巡检-yyyy-mm-dd.docx .

# RUN mkdir -p /app/data/screenshots /app/data/reports /app/data/logs /app/data/browser-state


# EXPOSE 8000

# CMD ["sh", "-c", "uvicorn app.main:app --host ${APP_HOST} --port ${APP_PORT}"]



FROM python:3.11-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    APP_DATA_DIR=/app/data \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    TZ=Asia/Shanghai \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk \
    fonts-wqy-zenhei \
    tzdata \
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

CMD ["sh", "-c", "uvicorn app.main:app --host ${APP_HOST} --port ${APP_PORT}"]
