FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    EASYOCR_MODULE_PATH=/opt/easyocr-models

RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir easyocr==1.7.2

RUN mkdir -p /opt/easyocr-models \
    && python -c "import easyocr; easyocr.Reader(['ch_tra', 'en'], gpu=False, model_storage_directory='/opt/easyocr-models', download_enabled=True, verbose=True)"

COPY tools/easyocr_baseline.py /opt/foodscan/easyocr_baseline.py

ENTRYPOINT ["python", "/opt/foodscan/easyocr_baseline.py"]
