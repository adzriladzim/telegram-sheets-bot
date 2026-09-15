FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Data (users.json, usage.json, logs) persist via Railway mount at /app/data
# (jangan tambah instruksi persist di Dockerfile — Railway menolaknya)

CMD ["python", "bot.py"]
