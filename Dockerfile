FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/data/bot.db

WORKDIR /app

COPY requirements.txt .
RUN pip install -q -r requirements.txt

COPY db.py report.py bot.py ./
# Каталог под базу создаём заранее: если диск Railway не подключён,
# /data всё равно должен существовать, иначе запись файла упадёт.
RUN mkdir -p /data


CMD ["python", "bot.py"]
