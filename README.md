# HA-mealmind

> **Branch `thinkcentre_dev` — development and testing.**
> This branch is for trying out changes before they go to the ThinkCentre deployment
> or are proposed to the shared repo. Expect work-in-progress
> code, test data and breaking changes. Don't deploy from this branch.

A fully local Home Assistant integration that suggests recipes and adapts to feedback.
Currently a Telegram bot that suggests dinners from a scraped recipe database.
See `docs/VISION.md` for the full idea.

## Quick start (dev)

Run everything from the repo root, since DB and profile paths are relative to it.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Scrape recipes into backend/src/db/recipes.db (URLs in backend/src/recipe_urls.txt)
python backend/src/recipe_scraper.py
python backend/src/recipe_scraper.py https://www.bbcgoodfood.com/recipes/<slug>   # one-off

# Run the bot (needs TELEGRAM_BOT_TOKEN in .env, and Ollama for profiled users)
python backend/src/bot/bot.py

# Web UI for browsing and adding recipes: http://localhost:8099
python backend/src/web/app.py
```

## Docker

```bash
cp .env.example .env      # fill in TELEGRAM_BOT_TOKEN
docker compose up -d --build
```

Starts two containers from one image: `mealmind-bot` and `mealmind-web`
(web UI on port 8099, change with `WEB_PORT` in `.env`). The web UI has no login,
so only use it on your home network or Tailscale.

Use a separate test bot token for this branch, so it doesn't conflict with the bot
running on the ThinkCentre (Telegram only allows one poller per token).
