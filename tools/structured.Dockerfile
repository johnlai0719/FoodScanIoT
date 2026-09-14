FROM foodscan-easyocr-baseline:latest

RUN python -m pip install --no-cache-dir \
    transformers==4.49.0 \
    pydantic==2.10.6 \
    sentencepiece==0.2.0

COPY tools/structured_pipeline.py /opt/foodscan/structured_pipeline.py

ENTRYPOINT ["python", "/opt/foodscan/structured_pipeline.py"]
