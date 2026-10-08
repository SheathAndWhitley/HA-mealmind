CREATE TABLE recipe_meta (
    recipe_id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    servings INT NOT NULL,
    preptime_minutes INT NOT NULL,
    cooktime_minutes INT NOT NULL,
    ingredients TEXT,
    instructions TEXT,
    continent VARCHAR(100),
    source VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE ingredients (
    ingredient_id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE
);

CREATE TABLE recipe_ingredients (
    recipe_ingredient_id INT PRIMARY KEY AUTO_INCREMENT,
    recipe_id SERIAL REFERENCES recipe_meta(recipe_id),
    ingredient_id SERIAL REFERENCES ingredients(ingredient_id),
    amount DECIMAL NOT NULL,
    unit VARCHAR(50) NOT NULL
);

CREATE_TABLE recipe_steps (
    step_id SERIAL PRIMARY KEY,
    recipe_id SERIAL REFERENCES recipe_meta(recipe_id),
    step_number INT NOT NULL,
    instruction TEXT NOT NULL
);

CREATE_TABLE tags (
    tag_id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE
    category VARCHAR(100)
)

CREATE TABLE recipe_tags (
    recipe_id SERIAL REFERENCES recipe_meta(recipe_id),
    tag_id SERIAL REFERENCES tags(tag_id),
    PRIMARY KEY (recipe_id, tag_id)
);
