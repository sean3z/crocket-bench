FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ./
ENV PROMETHEUS_MULTIPROC_DIR=/tmp/prom POOL_PER_WORKER=4 PYTHONUNBUFFERED=1
EXPOSE 8000
ENTRYPOINT ["sh", "-c", "rm -rf $PROMETHEUS_MULTIPROC_DIR && mkdir -p $PROMETHEUS_MULTIPROC_DIR && exec uvicorn app:app --host 0.0.0.0 --port 8000 --workers 4 --loop uvloop --http httptools --no-access-log --timeout-keep-alive 75"]
