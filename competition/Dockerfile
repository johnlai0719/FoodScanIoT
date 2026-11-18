FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/server:/app/tools
WORKDIR /app
COPY competition/requirements.lock.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY server /app/server
COPY tools /app/tools
COPY web/pipeline /app/web/pipeline
CMD ["python", "tools/pipeline_dashboard.py"]
