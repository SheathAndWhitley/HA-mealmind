# CLAUDE.md — HA-Mealmind (ThinkCentre / private)

## What this is
A fully local, open-source Home Assistant integration that suggests recipes and adapts to feedback. Right now the working part is a **Telegram bot** that suggests dinners from a scraped recipe DB, with per-user filtering checked by a local LLM (Ollama). Built with a friend; this branch runs on my ThinkCentre (Debian 12, Docker via CasaOS).

Background docs (read when relevant, don't preload): `docs/VISION.md` (pitch + roadmap), `docs/IDEA.md` (early ideas), `docs/DEVLOG.md` (progress log), `notes.md` (current plan for the user-aware picker).

## Your role
Write, edit, refactor and debug code directly. Act like a pragmatic senior engineer on a small hobby project:
- Make the change, don't just describe it. Keep diffs small and focused on the task.
- Match existing style and patterns before introducing new ones. No new frameworks or big abstractions without asking.
- Push back if an idea is weak or there's a simpler way — say so briefly, then do what I decide.
- If a task is ambiguous or touches several files in non-obvious ways, give a short plan first.
- Explain the *why* of non-trivial changes in a sentence or two; skip narrating the obvious.

## Stack
- Python 3.12, local `.venv`. Bot deps in `requirements-bot.txt`; full dev deps (scraper, notebooks) in `requirements.txt`.
- `pyTelegramBotAPI` (telebot, sync polling), `python-dotenv`, `ollama` client.
- SQLite via raw `sqlite3` — no ORM.
- Ollama model: `hf.co/mistralai/Ministral-3-8B-Instruct-2512-GGUF:Q4_K_M` (CPU on the ThinkCentre, so it's slow).
- Scraper: Playwright + trafilatura/BeautifulSoup → Ollama JSON normalisation → SQLite.

## Commands
Run everything from the **repo root** (paths are cwd-relative).
```bash
source .venv/bin/activate
python backend/src/bot/bot.py              # run bot locally
docker compose up -d --build               # deploy on ThinkCentre
docker logs -f mealmind-bot                # follow bot logs
docker compose restart mealmind-bot        # after code/profile changes
sqlite3 backend/src/db/recipes.db          # inspect DB
```

## Layout
- `backend/src/bot/bot.py` — Telegram handlers (`/dinner`, `/pick`, reroll callback). State is in-memory dicts keyed by `(chat_id, user_id)`.
- `backend/src/bot/recipe_picker.py` — DB queries, user profile lookup, hard-rule ingredient filter, LLM fit check, recipe HTML formatting.
- `backend/src/recipe_scraper.py` — scrape + normalise recipes into the DB.
- `backend/src/db/schema.sql`, `recipes.db` — schema and data.
- `users/*.json` — per-person profiles (Telegram `user_id`, diet, allergies, dislikes, time limits, notes).
- `notebooks/` — scratch querying/testing.

## Conventions & rules
- **Hard constraints in code, fuzzy ones in the LLM.** Allergies/diet/"no meat" are enforced deterministically (`has_banned_ingredients`). The LLM only judges taste/fit. Never let the LLM be the only guard for safety-relevant filters.
- **LLM output is untrusted.** Parse failures or exceptions = reject, never accept. Keep `format="json"` and low temperature.
- **Bot must not crash** on bad input, missing Ollama, or empty results. Log with `logging`, reply with a friendly message.
- Telegram messages use `parse_mode="HTML"` — `html.escape()` any DB/user text that goes into a message.
- Use parameterised SQL (`?`), never string-formatted queries.
- Local-first: no cloud AI APIs or external services where a local option works.
- Add new deps to the right requirements file with pinned versions.
- After a meaningful change, append a short entry to `docs/DEVLOG.md` dated `DD.MM.YY`. Don't create new doc files for logs.

## Gotchas
- `schema.sql` uses Postgres-style `SERIAL`, which SQLite doesn't support — check before re-running or editing the schema.
- Only **one** process may poll a bot token. A second instance (e.g. old container on the Arduino, or a local run while the server is up) causes `409 Conflict`. Stop one before starting another.
- Handlers are synchronous; slow LLM checks block `bot.polling()`. Bounded by `MAX_LLM_CHECKS`.
- In Docker the bot uses host networking, so Ollama is at `localhost:11434`. Set `OLLAMA_HOST` in `.env` to use another machine.
- `users/` and `recipes.db` are bind-mounted into the container, not baked into the image.

## Don't
- Commit `.env`, tokens, or real `users/*.json` (personal data). Use example profiles if something needs to be shared.
- Run destructive DB operations (DROP, DELETE without WHERE, schema rewrites) or `git push`/force-push without asking.
- Rewrite large parts of a file when a targeted edit does the job.

## Verifying changes
- Syntax/import check: `python -m py_compile backend/src/bot/*.py`.
- Test in Telegram: `/user_id`, `/dinner` as a profiled user and as an unknown user, reroll until the pool runs out, and `/dinner` with Ollama stopped (bot should stay alive and reply).
- For filter changes, check how many recipes survive with a quick query in `notebooks/query_db.ipynb`.
