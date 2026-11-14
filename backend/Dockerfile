FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
# APP_MODULE picks the service: the scan API (default) or app.review_service:app
# for the hosted review console (a second Railway service from this image).
CMD ["sh", "-c", "uvicorn ${APP_MODULE:-app.api:app} --host 0.0.0.0 --port ${PORT:-8000}"]