# CLAUDE.md — HA-Mealmind (`thinkcentre_dev`)

## This branch
`thinkcentre_dev` is my **development and testing** branch. Experiments, refactors and
work-in-progress code are expected here. Stable changes are later moved to the ThinkCentre
deployment or proposed to the shared repo (built with a friend). Don't treat anything here
as production, and don't touch the running ThinkCentre deployment from this branch.

## Your role
Write, edit, refactor and debug code directly. Act like a pragmatic senior engineer on a
small hobby project:
- Make the change, don't just describe it. Keep diffs focused on the task.
- Match existing style and patterns before introducing new ones. No new frameworks or
  big abstractions without asking.
- Push back if an idea is weak or there's a simpler way — say so briefly, then do what I decide.
- If a task is ambiguous or touches several files in non-obvious ways, give a short plan first.
- Explain the *why* of non-trivial changes in a sentence or two; skip narrating the obvious.

## What this is
A fully local, open-source Home Assistant integration that suggests recipes and adapts to
feedback. The working part today is a **Telegram bot** that suggests dinners from a scraped
recipe DB, with per-user filtering checked by a local LLM (Ollama).
Background (read when relevant): `docs/VISION.md`, `docs/IDEA.md`, `docs/DEVLOG.md`, `notes.md`.

## Stack
- Python 3.12, local `.venv`, deps in `requirements.txt` (pinned).
- Bot: `pyTelegramBotAPI` (sync polling), `python-dotenv`, `ollama`.
- Scraper: `curl_cffi` (HTTP with browser TLS fingerprints), `recipe-scrapers` (schema.org /
  site-specific parsing), `ingredient-parser-nlp` (amount/unit/name/prep). **No LLM.**
  Behaviour modelled on Mealie's scraper but written from scratch: Mealie is AGPL-3.0 and
  this repo is MIT, so **never copy code from Mealie** into this repo.
- Web UI: Flask + Jinja templates, served by `waitress`. Plain CSS, no JS framework or build step.
- SQLite via raw `sqlite3`, no ORM.
- Ollama model (bot only): `hf.co/mistralai/Ministral-3-8B-Instruct-2512-GGUF:Q4_K_M`, CPU, slow.

## Commands
Run everything from the **repo root** (paths are cwd-relative).
```bash
source .venv/bin/activate
python backend/src/recipe_scraper.py              # scrape URLs in backend/src/recipe_urls.txt
python backend/src/recipe_scraper.py URL [URL…]   # scrape specific URLs
python backend/src/recipe_scraper.py --file recipe_lists/bbc_good_food_dinners.md  # URLs in any .txt/.md
python backend/src/bot/bot.py                     # run the bot
python backend/src/web/app.py                     # web UI on http://localhost:8099
docker compose up -d --build                      # bot + web UI in Docker
sqlite3 backend/src/db/recipes.db                 # inspect DB
python -m py_compile backend/src/bot/*.py backend/src/*.py   # quick syntax check
```

## Layout
- `backend/src/recipe_scraper.py` — pipeline + DB: fetch (parallel) → `recipe-scrapers` →
  clean → ingredient parser → rule-based tags/continent/ingredient-step links → SQLite.
  Skips URLs already in the DB. `ensure_schema()` creates tables and adds new columns.
- `backend/src/scraper_fetch.py` — SSRF-safe fetch: blocks non-public addresses (also on
  redirects), rotates browser fingerprints on bot blocks, timeouts and a 5 MB size cap.
- `backend/src/scraper_clean.py` — cleaning for messy schema.org data (HTML/entities,
  HowToSections, ISO durations, yields, keywords, images, ingredient groups).
- `backend/src/recipe_urls.txt` — default URLs to scrape, one per line, `#` for comments.
- `recipe_lists/*.md` — larger URL lists (e.g. 225 BBC Good Food dinners, partly unverified).
  `extract_urls()` pulls every http(s) URL out of any text/markdown file.
- `backend/src/bot/bot.py` — Telegram handlers (`/dinner`, `/pick`, reroll). In-memory state
  keyed by `(chat_id, user_id)`.
- `backend/src/bot/recipe_picker.py` — DB queries, profile lookup, hard-rule ingredient filter,
  LLM fit check, recipe HTML formatting.
- `backend/src/web/` — web UI (`app.py`, `templates/`, `static/style.css`): browse/search
  recipes, view one, add by URL (reuses the scraper), by uploading a .txt/.md file of URLs
  (background import with a progress page, one import at a time, state kept in memory),
  or by hand; delete. No login. A Normal/Engineering toggle (cookie `view`) switches the
  recipe page to a "Cooking for Engineers" table built by `web/engineering.py` from the step
  graph (`next_step_id`) or, if missing, from step sections / step order.
- `Dockerfile`, `docker-compose.yml` — one image, two services: `bot` and `web`
  (`network_mode: host`, shared `backend/src/db` volume). Runtime deps in `requirements-app.txt`.
- `backend/src/db/recipes.db` — data. `schema.sql` is out of date (Postgres syntax); the
  scraper's `ensure_schema()` is the real schema.
- `users/*.json` — per-person profiles (Telegram `user_id`, diet, dislikes, time limits, notes).

## Conventions & rules
- **Hard constraints in code, fuzzy ones in the LLM.** Diet/allergy rules must be enforced
  deterministically; the LLM only judges taste/fit and is never the only safety guard.
- **LLM output is untrusted.** Parse failure or exception = reject.
- **The bot must not crash** on bad input, missing Ollama or empty results.
- Telegram uses `parse_mode="HTML"`: `html.escape()` any DB/user text in messages.
- Parameterised SQL (`?`) only. Lowercase tag names, categories and continents.
- Local-first: no cloud AI APIs or external services where a local option works.
- Pin new dependencies in `requirements.txt`.
- After a meaningful change, append a short dated entry (`DD.MM.YY`) to `docs/DEVLOG.md`.

## Gotchas
- Only **one** process may poll a bot token. Use a separate test bot token on this branch;
  otherwise you get `409 Conflict` with the deployed bot.
- `ingredient-parser-nlp` downloads NLTK tagger data on first run (needs internet once).
- Sites without schema.org recipe data are skipped by the scraper, not parsed.
- The scraper does not fill `recipe_steps.next_step_id` (step graph); that needed the LLM.
- Newer columns (`recipe_meta.image_url`, `recipe_steps.section`,
  `recipe_ingredients.original_text`, `recipe_ingredients.group_name`) are NULL for old rows.
- Sync handlers: slow LLM checks block the bot; bounded by `MAX_LLM_CHECKS`.

- The web UI has no authentication: keep it on the LAN/Tailscale, never behind a public tunnel.

## Don't
- Commit `.env`, tokens, or real `users/*.json` (personal data).
- Run destructive DB operations (DROP, DELETE without WHERE, schema rewrites) on
  `recipes.db` without asking — copy it first if you need to experiment.
- `git push` or force-push without asking.
