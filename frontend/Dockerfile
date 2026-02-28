# ── Stage 1: builder ──
FROM python:3.11-alpine AS builder
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade "pip>=26.0" "wheel>=0.46.2" setuptools
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Stage 2: runtime ──
FROM python:3.11-alpine AS runtime
RUN pip install --no-cache-dir --upgrade "pip>=26.0" "wheel>=0.46.2" setuptools
RUN adduser -D -h /home/appuser -s /bin/sh appuser
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /home/appuser/app
COPY --chown=appuser:appuser app.py .
USER appuser
EXPOSE 8501
ENTRYPOINT ["streamlit", "run", "app.py", \
    "--server.port=8501", \
    "--server.address=0.0.0.0", \
    "--server.headless=true", \
    "--server.runOnSave=false"]