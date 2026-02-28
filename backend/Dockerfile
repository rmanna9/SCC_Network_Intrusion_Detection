# ── Stage 1: builder ──
FROM python:3.11-slim-bookworm AS builder

# Fix CVE-2026-24049 (wheel 0.45.1 → 0.46.2) e CVE-2025-8869 (pip → 25.3)
RUN pip install --no-cache-dir --upgrade "pip>=25.3" "wheel>=0.46.2" setuptools

# curl necessario per l'healthcheck
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Esegui come utente non-root (best practice di sicurezza)
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]