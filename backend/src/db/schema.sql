CREATE TABLE recipe_meta (
    id SERIAL PRIMARY KEY,
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