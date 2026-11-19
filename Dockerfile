FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libpango*/libharfbuzz-subset0 = runtime WeasyPrint (render PDF surat);
# fonts-* = tipografi surat (Liberation = metrik Times New Roman/Arial).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        opensc pcscd libpcsclite1 curl openssl ca-certificates \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
        libcairo2 libgdk-pixbuf-2.0-0 shared-mime-info \
        fonts-dejavu-core fonts-liberation \
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
