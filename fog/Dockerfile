FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Fog Server 預設在 8081 埠
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8081"]
