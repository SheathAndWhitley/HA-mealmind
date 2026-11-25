"""Small web UI for the recipe DB: browse, view, add (by URL or by hand), delete.

Run from the repo root:
    python backend/src/web/app.py          # serves on 0.0.0.0:$WEB_PORT (default 8099)

No login: keep it on the home network / Tailscale, never expose it publicly.
"""

import os
import sqlite3
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, flash, redirect, render_template, request, url_for

# recipe_scraper.py lives one folder up (backend/src)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recipe_scraper  # noqa: E402

DB_PATH = recipe_scraper.DB_PATH
MAX_URLS_PER_SUBMIT = 10

app = Flask(__name__)


_db_ready = False


@app.before_request
def _prepare_db() -> None:
    """Create/migrate the schema once, so pages can rely on the newest columns."""
    global _db_ready
    if _db_ready:
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        recipe_scraper.ensure_schema(conn)
    finally:
        conn.close()
    _db_ready = True
app.secret_key = os.getenv("WEB_SECRET_KEY") or os.urandom(16)


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@app.template_filter("amount")
def format_amount(value) -> str:
    if value is None:
        return ""
    value = round(float(value), 2)
    return str(int(value)) if value.is_integer() else f"{value:g}"


@app.template_filter("cap")
def capitalize_first(text) -> str:
    text = str(text or "")
    return text[:1].upper() + text[1:]


# --------------------------------------------------------------------------- #
# Browse
# --------------------------------------------------------------------------- #

@app.route("/")
def index():
    q = request.args.get("q", "").strip().lower()
    tag = request.args.get("tag", "").strip().lower()

    sql = """
        SELECT m.recipe_id, m.title, m.description, m.preptime_minutes, m.cooktime_minutes,
               m.continent, m.servings, m.image_url,
               (SELECT group_concat(t.name, '|') FROM recipe_tags rt
                  JOIN tags t ON t.tag_id = rt.tag_id WHERE rt.recipe_id = m.recipe_id) AS tags
        FROM recipe_meta m
        WHERE 1 = 1
    """
    params = []
    if q:
        sql += """ AND (lower(m.title) LIKE ? OR EXISTS (
                     SELECT 1 FROM recipe_ingredients ri JOIN ingredients i
                       ON i.ingredient_id = ri.ingredient_id
                     WHERE ri.recipe_id = m.recipe_id AND i.name LIKE ?))"""
        params += [f"%{q}%", f"%{q}%"]
    if tag:
        sql += """ AND EXISTS (SELECT 1 FROM recipe_tags rt JOIN tags t ON t.tag_id = rt.tag_id
                               WHERE rt.recipe_id = m.recipe_id AND t.name = ?)"""
        params.append(tag)
    sql += " ORDER BY m.title"

    with get_db() as conn:
        recipes = conn.execute(sql, params).fetchall()
        top_tags = conn.execute("""
            SELECT t.name, count(*) AS n FROM recipe_tags rt JOIN tags t ON t.tag_id = rt.tag_id
            GROUP BY t.name ORDER BY n DESC, t.name LIMIT 15
        """).fetchall()
        total = conn.execute("SELECT count(*) FROM recipe_meta").fetchone()[0]
    return render_template("index.html", recipes=recipes, top_tags=top_tags,
                           q=q, tag=tag, total=total)


@app.route("/recipe/<int:recipe_id>")
def recipe(recipe_id: int):
    with get_db() as conn:
        meta = conn.execute("SELECT * FROM recipe_meta WHERE recipe_id = ?", (recipe_id,)).fetchone()
        if meta is None:
            abort(404)
        ingredients = conn.execute("""
            SELECT i.name, ri.amount, ri.unit, ri.prep_method, ri.original_text, ri.group_name
            FROM recipe_ingredients ri JOIN ingredients i ON i.ingredient_id = ri.ingredient_id
            WHERE ri.recipe_id = ? ORDER BY ri.recipe_ingredient_id
        """, (recipe_id,)).fetchall()
        steps = conn.execute("""
            SELECT step_number, instruction, duration_text, section FROM recipe_steps
            WHERE recipe_id = ? ORDER BY step_number
        """, (recipe_id,)).fetchall()
        tags = conn.execute("""
            SELECT t.name, t.category FROM recipe_tags rt JOIN tags t ON t.tag_id = rt.tag_id
            WHERE rt.recipe_id = ? ORDER BY t.category, t.name
        """, (recipe_id,)).fetchall()
    source_is_link = meta["source_url"].startswith(("http://", "https://"))
    return render_template("recipe.html", meta=meta, ingredients=ingredients, steps=steps,
                           tags=tags, source_is_link=source_is_link)


@app.post("/recipe/<int:recipe_id>/delete")
def delete_recipe(recipe_id: int):
    with get_db() as conn:
        title = conn.execute("SELECT title FROM recipe_meta WHERE recipe_id = ?",
                             (recipe_id,)).fetchone()
        conn.execute("DELETE FROM recipe_meta WHERE recipe_id = ?", (recipe_id,))
    if title:
        flash(f"Deleted “{title[0]}”.", "ok")
    return redirect(url_for("index"))


# --------------------------------------------------------------------------- #
# Add
# --------------------------------------------------------------------------- #

@app.get("/add")
def add():
    return render_template("add.html")


@app.post("/add/url")
def add_by_url():
    urls = [u.strip() for u in request.form.get("urls", "").splitlines() if u.strip()]
    urls = [u for u in urls if urlsplit(u).scheme in ("http", "https")][:MAX_URLS_PER_SUBMIT]
    if not urls:
        flash("Paste at least one http(s) recipe URL.", "error")
        return redirect(url_for("add"))

    conn = get_db()
    try:
        recipe_scraper.ensure_schema(conn)
        for url in urls:
            try:
                raw = recipe_scraper.extract_recipe(url)
                if raw is None:
                    flash(f"No recipe data found at {url}", "error")
                elif recipe_scraper.store_recipe_in_db(
                        conn, recipe_scraper.normalize_url(url),
                        recipe_scraper.to_recipe_data(raw)) is not None:
                    flash(f"Added “{raw['title']}”.", "ok")
                else:
                    flash(f"Already in the database: {url}", "info")
            except recipe_scraper.FetchError as e:
                flash(f"Could not add {url}: {e}", "error")
            except Exception as e:
                flash(f"Could not add {url}: {type(e).__name__}", "error")
    finally:
        conn.close()
    return redirect(url_for("index"))


def _int_field(name: str) -> int:
    try:
        return max(int(request.form.get(name, 0) or 0), 0)
    except ValueError:
        return 0


@app.post("/add/manual")
def add_manual():
    title = request.form.get("title", "").strip()
    ingredients = [l.strip() for l in request.form.get("ingredients", "").splitlines() if l.strip()]
    steps = [l.strip() for l in request.form.get("steps", "").splitlines() if l.strip()]
    if not title or not ingredients or not steps:
        flash("Title, ingredients and steps are required.", "error")
        return render_template("add.html", form=request.form, tab="manual")

    # Same "raw recipe" format extract_recipe() returns, so it goes through the same normalisation.
    clean = recipe_scraper.clean
    tags = clean.keyword_list(request.form.get("tags", ""))
    raw = {
        "title": title,
        "description": request.form.get("description", "").strip() or None,
        "servings": _int_field("servings"),
        "prep": _int_field("prep"),
        "cook": _int_field("cook"),
        "total": None,
        "cuisines": clean.keyword_list(request.form.get("cuisine", "")),
        "categories": clean.keyword_list(request.form.get("category", "")),
        "keywords": tags,
        "diets": [],
        "site": "manual",
        "image": clean.image_url(request.form.get("image", "")),
        "ingredient_groups": [(None, ingredients)],
        "steps": [{"text": step, "section": None} for step in steps],
    }
    recipe_data = recipe_scraper.to_recipe_data(raw)
    # Free-text tags: keep all of them, not just the known dietary/equipment words.
    known = {t["name"] for t in recipe_data["tags"]}
    recipe_data["tags"] += [{"name": t, "category": None} for t in tags if t not in known]

    conn = get_db()
    try:
        recipe_scraper.ensure_schema(conn)
        recipe_id = recipe_scraper.store_recipe_in_db(conn, f"manual://{uuid.uuid4()}", recipe_data)
    finally:
        conn.close()
    flash(f"Added “{title}”.", "ok")
    return redirect(url_for("recipe", recipe_id=recipe_id))


if __name__ == "__main__":
    from waitress import serve

    port = int(os.getenv("WEB_PORT", "8099"))
    print(f"MealMind web UI on http://0.0.0.0:{port}", flush=True)
    serve(app, host="0.0.0.0", port=port)
