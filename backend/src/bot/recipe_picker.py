import sqlite3

def random_dinners():
    db_path = "backend/src/db/recipes.db"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row # each row acts as a dict, not needing index
    cursor = conn.cursor()

    query = """SELECT recipe_id, title, description, preptime_minutes, cooktime_minutes, continent 
                FROM recipe_meta ORDER BY RANDOM() LIMIT 3 
                """

    cursor.execute(query)
    recipes = cursor.fetchall()

    dict_recipes = []
    for row in recipes:
        dict_recipes.append(dict(row))

    return dict_recipes

meta_recipes = random_dinners()

for line in meta_recipes:
    print(f"{line}\n")

def bot_options_message():
    recipe_options = random_dinners

    option_messages = []
    for recipe in recipe_options:
        title = recipe_options["title"]
        description = recipe_options["description"]
