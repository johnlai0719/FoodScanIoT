FROM python:3.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       tesseract-ocr \
       tesseract-ocr-chi-tra \
       tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir Pillow==11.3.0

WORKDIR /workspace
ENTRYPOINT ["python", "tools/tesseract_baseline.py"]
