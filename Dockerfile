FROM python:3.10-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       libpq-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && pip install gunicorn
COPY . .
RUN python manage.py collectstatic --noinput
EXPOSE 80
CMD ["gunicorn", "embackend.wsgi:application", "--bind", "0.0.0.0:80"]

