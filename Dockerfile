FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# /app/fonts (DejaVuSans untuk PDF laporan) ikut dari repo; refresh dari
# matplotlib bila font repo tidak tersedia (mis. .dockerignore mengecualikan).
RUN python -c "import shutil, pathlib, matplotlib; d = pathlib.Path('/app/fonts'); d.mkdir(exist_ok=True); s = pathlib.Path(matplotlib.get_data_path())/'fonts/ttf'; [(shutil.copy(s/n, d/n)) for n in ('DejaVuSans.ttf','DejaVuSans-Bold.ttf') if not (d/n).exists()]"

# Data (users.json, usage.json, logs) persist via Railway mount at /app/data
# (jangan tambah instruksi persist di Dockerfile — Railway menolaknya)

CMD ["python", "bot.py"]
