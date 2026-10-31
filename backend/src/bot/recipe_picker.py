import sqlite3
import html

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

    conn.close()

    return dict_recipes

def bot_options_message(random_dinners_dict):
    option_message = "Good morning! Here are your dinner options:\n\n"
    db_recipe_index = [recipe['recipe_id'] for recipe in random_dinners_dict]

    for index, recipe in enumerate(random_dinners_dict, start=1):
        recipe_message = f"<b>Option {index}</b>,\n<i>{recipe['title'].title()}</i>\n{recipe['description']}\nPrep time: {recipe['preptime_minutes']} minutes\nCook time: {recipe['cooktime_minutes']} minutes\nContinent: {recipe['continent']}\n\n"
        option_message += recipe_message

    return db_recipe_index, option_message