"""Production Gunicorn defaults, overridable through environment variables."""

import os


def _positive_int(name, default):
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw_value!r}") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be at least 1, got {value}")
    return value


bind = "0.0.0.0:8000"
worker_class = "gthread"
workers = _positive_int("GUNICORN_WORKERS", 2)
threads = _positive_int("GUNICORN_THREADS", 2)
timeout = _positive_int("GUNICORN_TIMEOUT", 30)
graceful_timeout = _positive_int("GUNICORN_GRACEFUL_TIMEOUT", 15)
keepalive = _positive_int("GUNICORN_KEEPALIVE", 5)
max_requests = _positive_int("GUNICORN_MAX_REQUESTS", 1000)
max_requests_jitter = _positive_int("GUNICORN_MAX_REQUESTS_JITTER", 100)

# Keep high-volume request logging off unless it is explicitly needed. When
# enabled with "-", Docker receives the access log on stdout.
accesslog = os.getenv("GUNICORN_ACCESS_LOG") or None
errorlog = "-"
capture_output = True
