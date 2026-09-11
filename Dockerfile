FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Data (users.json, usage.json, logs) — mount Railway Volume ke /app/data biar persist
VOLUME ["/app/data"]

CMD ["python", "bot.py"]
