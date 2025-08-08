FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app

# OS deps (PostgreSQL client for psycopg2, build-essential for wheels)
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential libpq-dev postgresql-client && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir gunicorn -r requirements.txt

# Copy project
COPY . .

# Helpful entrypoint – runs DB migrations & collectstatic only once
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "em_backend.wsgi:application", "--bind", "0.0.0.0:8000"]

