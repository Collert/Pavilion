FROM python:3.12-slim-bookworm

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ENVIRONMENT=production

# OpenCV (menu colour extraction), ffmpeg (TTS announcements), libpq (Postgres)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 ffmpeg libpq-dev gcc gettext \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/logs \
    && SECRET_KEY=build-only REDIS_URL=redis://unused python manage.py collectstatic --noinput \
    && SECRET_KEY=build-only REDIS_URL=redis://unused python manage.py compilemessages

EXPOSE 8000

# Migrate, then serve ASGI with several uvicorn workers. Redis (REDIS_URL) shares the cache and the
# live-update events between workers. WEB_CONCURRENCY sets the worker count.
CMD ["sh", "-c", "python manage.py migrate --noinput && exec uvicorn pavilion.asgi:application --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-3} --proxy-headers --forwarded-allow-ips='*'"]
