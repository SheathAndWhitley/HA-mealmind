"""Scrape recipes into the SQLite DB without an LLM.

Pipeline:
  1. scraper_fetch: SSRF-safe HTTP fetch that rotates browser TLS fingerprints
     when a site blocks us (no real browser needed).
  2. recipe-scrapers: site-specific scrapers, falling back to the page's
     schema.org recipe data.
  3. scraper_clean: tidy the messy parts (HTML in text, HowToSections, odd
     duration/yield formats, ingredient groups, keywords, images).
  4. ingredient-parser-nlp: split each ingredient line into amount/unit/name/prep
     (the original line is kept too).
  5. Rules for the extras: tags, continent, cooking times in steps, and the
     step each ingredient is first used in.

Fetching runs in parallel; parsing and DB writes run in the main thread.

Usage (from the repo root):
    python backend/src/recipe_scraper.py                  # URLs from recipe_urls.txt
    python backend/src/recipe_scraper.py URL [URL ...]    # specific URLs
    python backend/src/recipe_scraper.py --file PATH      # every URL in a .txt/.md file
"""

import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit

from ingredient_parser import parse_ingredient
from recipe_scrapers import scrape_html
from recipe_scrapers._exceptions import NoSchemaFoundInWildMode

import scraper_clean as clean
from scraper_fetch import FetchError, fetch_page

DB_PATH = "backend/src/db/recipes.db"
URLS_FILE = Path(__file__).with_name("recipe_urls.txt")

FETCH_WORKERS = 4        # parallel page downloads; keep it polite

UNIT_MAP = {
    "gram": "g", "kilogram": "kg", "milliliter": "ml", "millilitre": "ml",
    "liter": "l", "litre": "l", "centiliter": "cl", "deciliter": "dl",
    "teaspoon": "tsp", "tablespoon": "tbsp",
    "clove": "clove", "cloves": "clove", "pinch": "pinch", "pinches": "pinch",
}
UNIT_CONVERSIONS = {  # unit -> (factor, metric unit)
    "ounce": (28.35, "g"), "pound": (453.6, "g"),
    "cup": (240.0, "ml"), "fluid_ounce": (29.57, "ml"), "pint": (568.0, "ml"),
}

CUISINE_TO_CONTINENT = {
    "europe": ["italian", "french", "spanish", "greek", "british", "english", "irish",
               "scottish", "german", "portuguese", "scandinavian", "nordic", "norwegian",
               "swedish", "danish", "polish", "hungarian", "european", "mediterranean"],
    "asia": ["indian", "chinese", "japanese", "thai", "korean", "vietnamese", "indonesian",
             "malaysian", "filipino", "asian", "pakistani", "sri lankan", "middle eastern",
             "lebanese", "turkish", "persian", "israeli"],
    "north america": ["american", "mexican", "cajun", "caribbean", "jamaican", "canadian",
                      "tex-mex", "southern"],
    "south america": ["brazilian", "peruvian", "argentinian", "colombian", "latin american"],
    "africa": ["moroccan", "ethiopian", "african", "north african", "west african",
               "south african", "egyptian", "tunisian"],
    "oceania": ["australian", "new zealand", "hawaiian"],
}

DIETARY_TAGS = {"vegetarian", "vegan", "gluten-free", "dairy-free", "low-carb",
                "keto", "pescatarian", "nut-free", "egg-free", "low-fat", "low-calorie"}
EQUIPMENT_TAGS = {"oven", "air fryer", "slow cooker", "pressure cooker", "instant pot",
                  "wok", "blender", "food processor", "stand mixer", "dutch oven",
                  "barbecue", "bbq", "microwave", "grill"}

# Words too generic to link an ingredient to the step it's used in.
LINK_STOPWORDS = {"fresh", "large", "small", "medium", "ground", "dried", "chopped", "whole",
                  "extra", "virgin", "red", "green", "white", "black", "yellow", "brown",
                  "light", "dark", "low", "free", "and", "the", "for", "of", "or"}

DURATION_RE = re.compile(
    r"\b\d+(?:\s*(?:-|–|to)\s*\d+)?\s*(?:mins?|minutes?|hrs?|hours?|secs?|seconds?)\b",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# Fetch + extract (runs in worker threads, no shared state)
# --------------------------------------------------------------------------- #

def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    return f"{parts.scheme}://{parts.netloc.lower()}{parts.path.rstrip('/')}"


def _safe(func, default=None):
    """recipe-scrapers raises when a field is missing; treat that as 'no value'."""
    try:
        value = func()
    except Exception:
        return default
    return value if value not in (None, "", []) else default


def extract_recipe(url: str) -> dict | None:
    """Fetch a URL and return cleaned recipe fields, or None if it isn't a recipe.

    The returned dict is the "raw recipe" format that to_recipe_data() accepts
    (the web UI builds the same format for hand-entered recipes).
    """
    page = fetch_page(url)
    try:
        scraper = scrape_html(page.html, org_url=page.url, supported_only=False)
    except NoSchemaFoundInWildMode:
        return None

    try:
        schema = scraper.schema.data or {}
    except Exception:
        schema = {}

    title = clean.clean_text(_safe(scraper.title) or schema.get("name"))
    lines = clean.clean_ingredient_lines(_safe(scraper.ingredients) or schema.get("recipeIngredient"))
    steps = clean.choose_instructions(
        clean.parse_instructions(schema.get("recipeInstructions")),
        _safe(scraper.instructions_list, []),
    )
    if not title or not lines or not steps:
        return None

    prep = clean.duration_minutes(_safe(scraper.prep_time)) or clean.duration_minutes(schema.get("prepTime"))
    cook = (clean.duration_minutes(_safe(scraper.cook_time))
            or clean.duration_minutes(schema.get("cookTime") or schema.get("performTime")))
    total = clean.duration_minutes(_safe(scraper.total_time)) or clean.duration_minutes(schema.get("totalTime"))

    return {
        "title": title,
        "description": clean.clean_text(_safe(scraper.description) or schema.get("description")) or None,
        "servings": clean.servings(schema.get("recipeYield") or _safe(scraper.yields)),
        "prep": prep,
        "cook": cook,
        "total": total,
        "cuisines": clean.keyword_list(_safe(scraper.cuisine) or schema.get("recipeCuisine")),
        "categories": clean.keyword_list(_safe(scraper.category) or schema.get("recipeCategory")),
        "keywords": clean.keyword_list(_safe(scraper.keywords) or schema.get("keywords")),
        "diets": _safe(scraper.dietary_restrictions, []),
        "site": _safe(scraper.site_name) or _safe(scraper.host) or urlsplit(page.url).hostname,
        "image": clean.image_url(_safe(scraper.image) or schema.get("image")),
        "ingredient_groups": clean.ingredient_groups(_safe(scraper.ingredient_groups, []), lines),
        "steps": steps,
    }


# --------------------------------------------------------------------------- #
# Normalise into the DB shape (runs in the main thread)
# --------------------------------------------------------------------------- #

def _normalize_unit(quantity, unit) -> tuple[float | None, str | None]:
    """Map ingredient-parser's quantity/unit to the units used in the DB."""
    amount = float(quantity) if quantity not in (None, "") else None
    unit_text = str(unit).strip().lower() if unit else ""

    if not unit_text:
        return amount, ("whole" if amount is not None else None)
    if unit_text in UNIT_MAP:
        return amount, UNIT_MAP[unit_text]
    if unit_text in UNIT_CONVERSIONS:
        factor, metric = UNIT_CONVERSIONS[unit_text]
        return (round(amount * factor, 1) if amount is not None else None), metric
    return amount, unit_text  # e.g. "can", "bunch": keep as written


def parse_ingredient_line(line: str) -> list[dict]:
    """One ingredient line -> one or more ingredient rows ("soy and honey" -> two)."""
    try:
        parsed = parse_ingredient(line)
    except Exception:
        return [{"name": line.strip().lower(), "amount": None, "unit": None, "prep_method": None,
                 "original_text": line}]

    amount, unit = (None, None)
    if parsed.amount:
        first = parsed.amount[0]
        amount, unit = _normalize_unit(getattr(first, "quantity", None), getattr(first, "unit", None))

    prep = parsed.preparation.text if parsed.preparation else None
    names = [n.text.strip().lower() for n in parsed.name if n.text.strip()] or [line.strip().lower()]
    return [{"name": n, "amount": amount, "unit": unit, "prep_method": prep, "original_text": line}
            for n in names]


def _link_words(name: str) -> list[str]:
    words = re.findall(r"[a-zæøå]+", name)
    keys = [w for w in words if len(w) > 2 and w not in LINK_STOPWORDS]
    # match "chillies" in the name against "chilli" in the step, and vice versa
    return [w[:-3] if w.endswith("ies") else w.rstrip("s") for w in keys]


def first_step_using(name: str, steps: list[dict]) -> int | None:
    """Step number where an ingredient is first mentioned, by simple word match."""
    keys = _link_words(name)
    if not keys:
        return None
    for step in steps:
        text = step["instruction"].lower()
        if any(re.search(rf"\b{re.escape(k)}", text) for k in keys):
            return step["step_number"]
    return None


def _continent(cuisines: list[str]) -> str | None:
    for cuisine in cuisines:
        for continent, members in CUISINE_TO_CONTINENT.items():
            if cuisine in members:
                return continent
    return None


def _diet_tag(diet: str) -> str:
    # "Gluten Free Diet" / "GlutenFreeDiet" -> "gluten-free"
    diet = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", diet).lower().replace("diet", "").strip()
    return diet.replace(" ", "-")


def build_tags(raw: dict) -> list[dict]:
    tags = {}
    for c in raw["cuisines"]:
        tags[c] = "cuisine"
    for c in raw["categories"]:
        tags.setdefault(c, "meal type")
    for d in raw["diets"]:
        tags.setdefault(_diet_tag(d), "dietary")
    for kw in raw["keywords"]:
        if kw in DIETARY_TAGS:
            tags.setdefault(kw, "dietary")
        elif kw in EQUIPMENT_TAGS:
            tags.setdefault(kw, "equipment")
    return [{"name": name, "category": category} for name, category in tags.items()]


def to_recipe_data(raw: dict) -> dict:
    """Turn a raw recipe (see extract_recipe) into the dict store_recipe_in_db expects."""
    steps = []
    for number, step in enumerate(raw["steps"], start=1):
        duration = DURATION_RE.search(step["text"])
        steps.append({
            "step_number": number,
            "instruction": step["text"],
            "section": step.get("section"),
            "next_step_number": None,  # step graph not derived without an LLM
            "duration_text": duration.group() if duration else None,
        })

    ingredients = []
    for group, lines in raw["ingredient_groups"]:
        for line in lines:
            for ing in parse_ingredient_line(line):
                ing["group_name"] = group
                ing["step_number"] = first_step_using(ing["name"], steps)
                ingredients.append(ing)

    prep, cook, total = raw.get("prep") or 0, raw.get("cook") or 0, raw.get("total") or 0
    if prep == 0 and cook == 0:
        cook = total
    elif cook == 0 and total > prep:
        cook = total - prep

    return {
        "recipe_meta": {
            "title": raw["title"].strip().lower(),
            "description": raw.get("description"),
            "servings": raw.get("servings") or 0,
            "preptime_minutes": prep,
            "cooktime_minutes": cook,
            "continent": _continent(raw["cuisines"]),
            "source": raw.get("site"),
            "image_url": raw.get("image"),
        },
        "recipe_ingredients": ingredients,
        "recipe_steps": steps,
        "tags": build_tags(raw),
    }


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #

def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS recipe_meta (
            recipe_id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            servings INTEGER NOT NULL DEFAULT 0,
            preptime_minutes INTEGER NOT NULL DEFAULT 0,
            cooktime_minutes INTEGER NOT NULL DEFAULT 0,
            continent TEXT,
            source TEXT,
            source_url TEXT NOT NULL UNIQUE,
            image_url TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS ingredients (
            ingredient_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS recipe_steps (
            step_id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL REFERENCES recipe_meta(recipe_id) ON DELETE CASCADE,
            step_number INTEGER NOT NULL,
            instruction TEXT NOT NULL,
            next_step_id INTEGER REFERENCES recipe_steps(step_id) ON DELETE SET NULL,
            duration_text TEXT,
            section TEXT,
            UNIQUE (recipe_id, step_number)
        );
        CREATE TABLE IF NOT EXISTS recipe_ingredients (
            recipe_ingredient_id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL REFERENCES recipe_meta(recipe_id) ON DELETE CASCADE,
            ingredient_id INTEGER NOT NULL REFERENCES ingredients(ingredient_id) ON DELETE CASCADE,
            amount REAL,
            unit TEXT,
            prep_method TEXT,
            step_id INTEGER REFERENCES recipe_steps(step_id) ON DELETE SET NULL,
            original_text TEXT,
            group_name TEXT
        );
        CREATE TABLE IF NOT EXISTS tags (
            tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            category TEXT
        );
        CREATE TABLE IF NOT EXISTS recipe_tags (
            recipe_id INTEGER NOT NULL REFERENCES recipe_meta(recipe_id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tags(tag_id) ON DELETE CASCADE,
            PRIMARY KEY (recipe_id, tag_id)
        );
    ''')
    _migrate(conn)
    # Older rows were written with mixed-case categories ("Dietary" vs "dietary").
    with conn:
        conn.execute("UPDATE tags SET category = lower(category) WHERE category != lower(category)")


# Columns added after the first version of the schema: (table, column, type).
ADDED_COLUMNS = [
    ("recipe_meta", "image_url", "TEXT"),
    ("recipe_steps", "section", "TEXT"),
    ("recipe_ingredients", "original_text", "TEXT"),
    ("recipe_ingredients", "group_name", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    """Add new nullable columns to an existing DB; old rows just get NULL."""
    with conn:
        for table, column, col_type in ADDED_COLUMNS:
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def _get_or_create(cursor: sqlite3.Cursor, table: str, id_col: str, name: str, category=None) -> int:
    if table == "tags":
        cursor.execute("INSERT INTO tags (name, category) VALUES (?, ?) ON CONFLICT(name) DO NOTHING",
                       (name, category))
    else:
        cursor.execute("INSERT INTO ingredients (name) VALUES (?) ON CONFLICT(name) DO NOTHING", (name,))
    cursor.execute(f"SELECT {id_col} FROM {table} WHERE name = ?", (name,))
    return cursor.fetchone()[0]

def store_recipe_in_db(conn: sqlite3.Connection, normalized_url: str, recipe_data: dict) -> int | None:
    """Insert one recipe in a single transaction.

    Returns the new recipe_id, or None if the URL was already in the DB.
    """
    meta = recipe_data["recipe_meta"]
    with conn:  # commits on success, rolls back on any exception
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO recipe_meta (title, description, servings, preptime_minutes,
                                     cooktime_minutes, continent, source, source_url, image_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_url) DO NOTHING
        ''', (meta["title"], meta["description"], meta["servings"], meta["preptime_minutes"],
              meta["cooktime_minutes"], meta["continent"], meta["source"], normalized_url,
              meta.get("image_url")))
        if cursor.rowcount != 1:
            return None
        recipe_id = cursor.lastrowid

        step_id_by_number = {}
        for step in recipe_data["recipe_steps"]:
            cursor.execute('''
                INSERT INTO recipe_steps (recipe_id, step_number, instruction, duration_text, section)
                VALUES (?, ?, ?, ?, ?)
            ''', (recipe_id, step["step_number"], step["instruction"], step["duration_text"],
                  step.get("section")))
            step_id_by_number[step["step_number"]] = cursor.lastrowid

        for ing in recipe_data["recipe_ingredients"]:
            ingredient_id = _get_or_create(cursor, "ingredients", "ingredient_id", ing["name"])
            cursor.execute('''
                INSERT INTO recipe_ingredients (recipe_id, ingredient_id, amount, unit, prep_method,
                                                step_id, original_text, group_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (recipe_id, ingredient_id, ing["amount"], ing["unit"], ing["prep_method"],
                  step_id_by_number.get(ing.get("step_number")), ing.get("original_text"),
                  ing.get("group_name")))

        for tag in recipe_data["tags"]:
            tag_id = _get_or_create(cursor, "tags", "tag_id", tag["name"], tag["category"])
            cursor.execute('''
                INSERT INTO recipe_tags (recipe_id, tag_id) VALUES (?, ?)
                ON CONFLICT(recipe_id, tag_id) DO NOTHING
            ''', (recipe_id, tag_id))
    return recipe_id


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

URL_RE = re.compile(r"https?://[^\s<>\"'`)\]]+")


def extract_urls(text: str) -> list[str]:
    """All http(s) URLs in a text/markdown/CSV file, in order, without duplicates.

    Lines starting with # (comments in .txt files) are ignored; markdown headings
    contain no URLs, so they're skipped naturally.
    """
    urls, seen = [], set()
    for line in text.splitlines():
        if line.lstrip().startswith("#") and "://" in line:
            continue
        for match in URL_RE.findall(line):
            url = match.rstrip(".,;:*_")
            key = normalize_url(url)
            if key not in seen:
                seen.add(key)
                urls.append(url)
    return urls


def load_urls(args: list[str]) -> list[str]:
    """URLs from the command line: `--file PATH` (any text/markdown file) or URLs."""
    if args[:1] == ["--file"]:
        if len(args) < 2:
            raise SystemExit("usage: recipe_scraper.py --file PATH")
        return extract_urls(Path(args[1]).read_text(encoding="utf-8"))
    if args:
        return args
    return extract_urls(URLS_FILE.read_text(encoding="utf-8"))


def scrape_urls(urls: list[str], db_path: str = DB_PATH, on_result=None) -> dict:
    """Scrape URLs into the DB and return counts.

    on_result(status, url, detail) is called for every URL, where status is one of
    "known" (already in DB), "ok", "skip" (no recipe data) or "failed".
    """
    report = on_result or (lambda status, url, detail: None)
    counts = {"total": len(urls), "known": 0, "ok": 0, "skip": 0, "failed": 0}

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        ensure_schema(conn)
        known = {row[0] for row in conn.execute("SELECT source_url FROM recipe_meta")}
        todo = {}
        for url in urls:
            normalized = normalize_url(url)
            if normalized in known or normalized in todo:
                counts["known"] += 1
                report("known", url, None)
            else:
                todo[normalized] = url

        with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
            futures = {pool.submit(extract_recipe, url): (norm, url) for norm, url in todo.items()}
            for future in as_completed(futures):
                normalized, url = futures[future]
                try:
                    raw = future.result()
                    if raw is None:
                        status, detail = "skip", "no recipe data found"
                    elif store_recipe_in_db(conn, normalized, to_recipe_data(raw)) is not None:
                        status, detail = "ok", raw["title"]
                    else:
                        status, detail = "known", None
                except FetchError as e:
                    status, detail = "failed", str(e)
                except Exception as e:
                    status, detail = "failed", f"{type(e).__name__}: {e}"
                counts[status] += 1
                report(status, url, detail)
    finally:
        conn.close()
    return counts


def scrape_all(urls: list[str], db_path: str = DB_PATH) -> None:
    """Command-line wrapper around scrape_urls that prints progress."""
    start = time.time()
    labels = {"ok": "OK    ", "skip": "SKIP  ", "failed": "FAILED"}

    def show(status, url, detail):
        if status in labels:
            print(f"  {labels[status]} {url}" + (f" ({detail})" if detail else ""), flush=True)

    print(f"Scraping {len(urls)} URLs...", flush=True)
    c = scrape_urls(urls, db_path, show)
    print(f"Done in {time.time() - start:.1f}s: {c['ok']} stored, {c['known']} already in DB, "
          f"{c['skip']} skipped, {c['failed']} failed", flush=True)


if __name__ == "__main__":
    scrape_all(load_urls(sys.argv[1:]))
