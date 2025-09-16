FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libpq5 \
    postgresql-client \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir \
    gunicorn \
    psycopg \
    -r requirements.txt

# Copy project
COPY . .

# Give execution rights to the cron job
COPY crontab /etc/cron.d/zp_verify_cron
RUN chmod 0644 /etc/cron.d/zp_verify_cron

# Helpful entrypoint – runs DB migrations & collectstatic only once
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "em_backend.wsgi:application", "--bind", "0.0.0.0:8000"]

