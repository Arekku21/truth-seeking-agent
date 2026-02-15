FROM python:3.12-slim

# for logfire and celery logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set work directory
WORKDIR /app

# Install system dependencies (needed for some python packages)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpython3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for better caching)
COPY requirements.txt .

RUN pip install --upgrade pip

# Install Python dependencies
RUN pip install --no-cache-dir --default-timeout=1000 -r requirements.txt

# Copy the rest of the application code
COPY . .

#run the celery
CMD ["celery", "-A", "celery_config", "worker", "--loglevel=info", "--concurrency=8"]