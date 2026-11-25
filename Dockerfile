FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Repo root inside the container; DB_PATH and USERS_DIR are relative to it
WORKDIR /app

COPY requirements-bot.txt .
RUN pip install --no-cache-dir -r requirements-bot.txt

COPY backend/ backend/

CMD ["python", "backend/src/bot/bot.py"]
