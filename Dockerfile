FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        opensc pcscd libpcsclite1 curl openssl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tte_core ./tte_core
COPY ca ./ca

RUN useradd -m -u 10001 tte \
    && mkdir -p /data \
    && chown -R tte:tte /app /data

USER tte

EXPOSE 8000

CMD ["uvicorn", "tte_core.app:app", "--host", "0.0.0.0", "--port", "8000"]
