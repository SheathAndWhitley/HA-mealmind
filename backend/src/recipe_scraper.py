import json
import sqlite3
import trafilatura
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
import ollama

DB_PATH = "backend/src/db/recipes.db"

PROMPT = """
        You are a Data Normalization AI for a recipe pipeline. Extract unstructured recipe text (HTML or raw text) into a single, valid JSON object matching the structure below.
        ### TARGET JSON STRUCTURE
        {
        "recipe_meta": {
            "title": "String",
            "description": "String or null",
            "servings": "Integer (default to 0 if unspecified)",
            "preptime_minutes": "Integer (default to 0 if unspecified)",
            "cooktime_minutes": "Integer (default to 0 if unspecified)",
            "continent": "Asia | Europe | North America | South America | Africa | Oceania | null",
            "source": "String or null"
        },
        "recipe_ingredients": [
            {
            "name": "String (lowercase, base generic ingredient)",
            "amount": "Float or null",
            "unit": "g | kg | ml | l | tsp | tbsp | whole | clove | pinch | null"
            }
        ],
        "recipe_steps": [
            {
            "step_number": 1,
            "instruction": "String"
            }
        ],
        "tags": [
            {
            "name": "String (lowercase, concise tag name)",
            "category": "Dietary | Cuisine | Meal Type | Equipment | null"
            }
        ]
        }

        ### NORMALIZATION RULES
1. UNITS & CONVERSIONS:
- Weights -> 'g' or 'kg'. Liquid volumes -> 'ml' or 'l'.
- Temperatures in step instructions -> Convert °F to °C (e.g., 350°F -> 180°C).
- Fractions -> Decimals (e.g., "1 1/2" -> 1.5).
- Whole items (e.g., "1 onion") -> amount: 1.0, unit: "whole".
- Unmeasured items (e.g., "salt to taste") -> amount: null, unit: null.

2. INGREDIENT CLEANING & EXTRACTION:
- You MUST extract EVERY single ingredient mentioned in the recipe list OR step instructions. Do not omit water, flour, salt, or other basic ingredients.
- Remove prep methods ("diced", "chopped"), brands, and descriptors from "name".
- Examples:
- "1 large brown onion, finely diced" -> name: "yellow onion"
- "2 tbsp extra virgin olive oil" -> name: "olive oil"

3. TAG STANDARDIZATION & CATEGORIES:
- "Dietary": ONLY established diets (e.g., "gluten-free", "vegan", "vegetarian", "dairy-free", "low-carb").
- "Cuisine": Regional/cultural styles (e.g., "italian", "japanese", "german", "mexican").
- "Meal Type": Course or category (e.g., "breakfast", "dinner", "snack", "bread", "dessert", "side dish").
- "Equipment": Major kitchen tools or appliances (e.g., "oven", "air fryer", "blender", "stand mixer", "dutch oven").
- TAG RESTRICTIONS:
- "name" MUST be a concrete item/style (e.g., "vegan", "bread", "oven"). NEVER use the category names themselves (e.g., NEVER name: "dietary", "cuisine", "meal type").
- DO NOT tag instructions, actions, or environments (e.g., NEVER "warm place", "yeast based", "kneading").
- DO NOT tag textures, generic descriptors, or macros (e.g., NEVER "dense", "carbs", "fluffy", "tasty").
- Keep tag names lowercase, concise, and generic (e.g., "bread", not "rye bread").

4. OUTPUT & CONSTRAINTS:
- Use JSON null (without quotes), NEVER the text string "null".
- The "unit" key MUST ONLY use values from the allowed unit list. If a unit is non-standard (e.g., "thumb-sized piece"), convert it to 'whole' or set "unit": null.
- Ensure ingredient names remove prep states like "cooked", "canned", "sliced", or "frozen" (e.g., "cooked salmon fillet" -> "salmon fillet").
    """


def extract_json_ld(soup: BeautifulSoup) -> str | None:
    """Extracts JSON-LD recipe schema"""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            items = data if isinstance(data, list) else data.get("@graph", [data])
            for item in items:
                if item.get("@type") == "Recipe" or "Recipe" in item.get("@type", []):
                    return json.dumps(item)
        except (json.JSONDecodeError, TypeError, AttributeError):
            continue
    return None


def receive_and_clean(url: str) -> str:
    """Fetches web content using Playwright and cleans HTML via JSON-LD or Trafilatura and BS4."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        html_content = page.content()
        browser.close()

    soup = BeautifulSoup(html_content, "html.parser")

    # Checks for json-ld structured data first
    json_ld = extract_json_ld(soup)
    if json_ld:
        return json_ld

    # If no json-ld, cleans html with trafilatura and BS4
    for tag in soup(["script", "style", "svg", "iframe", "button", "form", "noscript", "footer", "nav"]):
        tag.decompose()

    pruned_content = str(soup)

    clean_content = trafilatura.extract(
        pruned_content,
        include_links=False,
        favor_precision=False,
        include_images=False,
        include_comments=False,
        include_tables=False,
        include_formatting=True,
        output_format="markdown"
    )

    return clean_content


def scrape_with_llm(clean_content: str) -> dict:
    """Uses local llm to turn the md string into structured json"""
    response = ollama.chat(
        model="hf.co/mistralai/Ministral-3-8B-Instruct-2512-GGUF:Q4_K_M", 
        format="json", 
        messages=[{"role": "user", "content": PROMPT + clean_content}]
    )

    #Converts response to a python readable dict
    json_string = response.message.content
    recipe_data = json.loads(json_string)

    return recipe_data

def store_recipe_in_db(recipe_data: dict, db_path: path = DB_PATH) -> None:
    """Stores the normalized recipe data in a SQLite database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Enable foreign keys in SQLite
    cursor.execute("PRAGMA foreign_keys = ON;")

    # 1. Create tables with native SQLite syntax
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS recipe_meta (
            recipe_id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            servings INTEGER NOT NULL DEFAULT 0,
            preptime_minutes INTEGER NOT NULL DEFAULT 0,
            cooktime_minutes INTEGER NOT NULL DEFAULT 0,
            continent TEXT,
            source TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ingredients (
            ingredient_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS recipe_ingredients (
            recipe_ingredient_id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL REFERENCES recipe_meta(recipe_id) ON DELETE CASCADE,
            ingredient_id INTEGER NOT NULL REFERENCES ingredients(ingredient_id) ON DELETE CASCADE,
            amount REAL,
            unit TEXT
        );
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS recipe_steps (
            step_id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL REFERENCES recipe_meta(recipe_id) ON DELETE CASCADE,
            step_number INTEGER NOT NULL,
            instruction TEXT NOT NULL,
            UNIQUE (recipe_id, step_number)
        );
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tags (
            tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            category TEXT
        );
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS recipe_tags (
            recipe_id INTEGER NOT NULL REFERENCES recipe_meta(recipe_id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tags(tag_id) ON DELETE CASCADE,
            PRIMARY KEY (recipe_id, tag_id)
        );
    ''')

    # 2. Insert Recipe Metadata
    meta = recipe_data.get("recipe_meta", {})
    cursor.execute('''
        INSERT INTO recipe_meta (title, description, servings, preptime_minutes, cooktime_minutes, continent, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        meta.get("title"),
        meta.get("description"),
        meta.get("servings", 0),
        meta.get("preptime_minutes", 0),
        meta.get("cooktime_minutes", 0),
        meta.get("continent"),
        meta.get("source")
    ))
    recipe_id = cursor.lastrowid

    # 3. Insert Ingredients safely (deduplicated & normalized)
    for ing in recipe_data.get("recipe_ingredients", []):
        raw_name = ing.get("name")
        if not raw_name or not raw_name.strip():
            continue

        ing_name = raw_name.strip().lower()

        cursor.execute('''
            INSERT INTO ingredients (name) VALUES (?)
            ON CONFLICT(name) DO NOTHING
        ''', (ing_name,))

        cursor.execute('SELECT ingredient_id FROM ingredients WHERE name = ?', (ing_name,))
        row = cursor.fetchone()
        if not row:
            continue
        ingredient_id = row[0]

        cursor.execute('''
            INSERT INTO recipe_ingredients (recipe_id, ingredient_id, amount, unit)
            VALUES (?, ?, ?, ?)
        ''', (
            recipe_id,
            ingredient_id,
            ing.get("amount"),
            ing.get("unit")
        ))

    # 4. Insert Steps
    for step in recipe_data.get("recipe_steps", []):
        cursor.execute('''
            INSERT INTO recipe_steps (recipe_id, step_number, instruction)
            VALUES (?, ?, ?)
        ''', (
            recipe_id,
            step.get("step_number"),
            step.get("instruction")
        ))

    # 5. Insert Tags safely
    for tag in recipe_data.get("tags", []):
        raw_tag = tag.get("name")
        if not raw_tag or not raw_tag.strip():
            continue

        tag_name = raw_tag.strip().lower()

        cursor.execute('''
            INSERT INTO tags (name, category) VALUES (?, ?)
            ON CONFLICT(name) DO NOTHING
        ''', (tag_name, tag.get("category")))

        cursor.execute('SELECT tag_id FROM tags WHERE name = ?', (tag_name,))
        row = cursor.fetchone()
        if not row:
            continue
        tag_id = row[0]

        cursor.execute('''
            INSERT INTO recipe_tags (recipe_id, tag_id) VALUES (?, ?)
            ON CONFLICT(recipe_id, tag_id) DO NOTHING
        ''', (recipe_id, tag_id))

    conn.commit()
    conn.close()


def scrape_recipe(url: str) -> None:
    """Orginization function to complete entire scrape to normalization"""
    clean_content = receive_and_clean(url)
    response = scrape_with_llm(clean_content)
    store_recipe_in_db(response)


scrape_recipe("https://grandmasvintagerecipes.blogspot.com/2026/10/homemade-pumpernickel-bread.html")