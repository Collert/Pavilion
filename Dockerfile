# Use official Python 3.9 image
FROM python:3.9

# Set working directory
WORKDIR /app

# Copy project files into container
COPY . .

# Install system dependencies for OpenCV and FFmpeg
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    ffmpeg

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose port 8000 for Django
EXPOSE 8000

# Run the Django application
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
