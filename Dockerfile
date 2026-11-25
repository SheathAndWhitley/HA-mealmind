FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NLTK_DATA=/usr/local/share/nltk_data

# Repo root inside the container; DB_PATH and USERS_DIR are relative to it
WORKDIR /app

COPY requirements-app.txt .
RUN pip install --no-cache-dir -r requirements-app.txt \
 && python -c "import nltk; nltk.download('averaged_perceptron_tagger_eng', download_dir='/usr/local/share/nltk_data', quiet=True)"

COPY backend/ backend/

# Default: the Telegram bot. The web service overrides this in docker-compose.yml.
CMD ["python", "backend/src/bot/bot.py"]
