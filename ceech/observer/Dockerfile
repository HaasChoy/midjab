# Use Python 3.11 slim base image for smaller footprint
FROM python:3.11-slim

# Set working directory inside container
WORKDIR /app

# Install system dependencies needed for pynput
# These are required for keyboard/mouse monitoring on Linux
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-tk \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file first (Docker layer caching optimization)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the source code
COPY observer.py .

# Set the command to run the observer script
CMD ["python", "observer.py"]