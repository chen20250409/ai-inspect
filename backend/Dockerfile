# =====================================================================
# AI 视觉质检后端镜像
# 构建：docker build -t fastapi-ai:v1 .
# =====================================================================
FROM python:3.11-slim

# 不改写 .pyc、日志实时输出，方便 docker logs 观察
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 先拷依赖清单再安装 —— 利用镜像层缓存，改代码时不必重装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
        -i https://mirrors.aliyun.com/pypi/simple/ \
        --trusted-host mirrors.aliyun.com

# 再拷业务代码
COPY main.py .

# 容器内上传目录（运行时会被宿主机 upload_img 目录挂载覆盖）
RUN mkdir -p /app/upload_img

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
