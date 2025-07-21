# Use official Python 3.9 image
FROM python:3.11.8-slim-bullseye

# Set working directory
WORKDIR /app

# Copy project files into container
COPY . .

COPY prod.env /app/pavilion/.env

# Install system dependencies for OpenCV, FFmpeg, and PostgreSQL
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    ffmpeg \
    libpq-dev \
    gcc \
    && apt-get upgrade -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Create directories for static files and logs
RUN mkdir -p /app/static /app/logs

# Collect static files
RUN python manage.py collectstatic --noinput || true

# Expose port 8000 for Django
EXPOSE 8000

# Create a startup script
RUN echo '#!/bin/bash\n\
    python manage.py migrate\n\
    python manage.py collectstatic --noinput\n\
    python manage.py runserver 0.0.0.0:8000' > /app/start.sh && chmod +x /app/start.sh

# Run the Django application
CMD ["/app/start.sh"]
