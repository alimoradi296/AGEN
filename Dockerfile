FROM python:3.12-slim

# Prevent .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies required by psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        "Django>=4.2" \
        "celery>=5.3" \
        "psycopg2-binary>=2.9" \
        "django-celery-beat>=2.5" \
        "redis>=4.6"

# Copy source code
COPY . .

# Default command (overridden by docker-compose per service)
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
