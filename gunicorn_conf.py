# gunicorn_conf.py
bind = "127.0.0.1:8000"
workers = 1
threads = 4  # Número de threads por worker
timeout = 60
graceful_timeout = 30
max_requests = 1000
max_requests_jitter = 50
loglevel = "info"
