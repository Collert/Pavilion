# Use official Python 3.9 image
FROM python:3.11.8-slim-bullseye

# Set working directory
WORKDIR /app

# Copy project files into container
COPY . .

COPY prod.env /app/pavilion/.env

# Install system dependencies for OpenCV and FFmpeg
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    ffmpeg \
    && apt-get upgrade -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose port 8000 for Django
EXPOSE 8000

# Run the Django application
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
