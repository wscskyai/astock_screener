# A 股选股系统 —— 镜像定义
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    ASTOCK_DB=/app/data/astock.db

# tzdata：容器内时间要对（分时/交易时段判断都按本地时间）
# curl：健康检查用
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata curl \
 && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
 && echo $TZ > /etc/timezone \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖，改动代码时不用重装（层缓存）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data

EXPOSE 5001

# 首页起来就说明服务可用（/api/me 不登录也返回 200）
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:5001/api/me > /dev/null || exit 1

CMD ["python", "-u", "web/app.py", "--host", "0.0.0.0", "--port", "5001", "--no-browser"]
