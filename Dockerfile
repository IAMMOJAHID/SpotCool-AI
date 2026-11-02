FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && adduser --disabled-password --gecos "" --uid 10001 appuser

COPY --chown=appuser:appuser app/worker.py ./
COPY --chown=appuser:appuser app/mvp_pipeline.py ./
COPY --chown=appuser:appuser app/spotcool ./spotcool

USER appuser

CMD ["python", "worker.py"]
