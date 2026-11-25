# DEVLOG, HA-Mealmind

## 02.10.26

### TODO

- Put scope of project into writing for our own reference, in dedicated VISION.md file.
  Should include everything from features, hardware and diagrams.
- Create a .TeX file to "pitch" the idea.



08.10.26

* Add tables to SQL database
  * Ingredients, steps and tags

## 09.10.26

### Done
- Finished the recipe scraper, and have begun populating db
- See recipe_scraper.py
- Also made a notebooks folder for testing code. populated with query_db.ipynb, for querying recipes.db



## 25.11.26

### Done
- Rewrote `recipe_scraper.py` without the LLM (branch `thinkcentre_dev`), modelled on Mealie:
  `curl_cffi` fetch instead of Playwright, `recipe-scrapers` for the schema.org data,
  `ingredient-parser-nlp` for amount/unit/name/prep. Pages are fetched in parallel and
  already-scraped URLs are skipped.
- Tags, continent and ingredient -> step links are now rule-based. Tag categories normalised
  to lowercase. Step graph (`next_step_id`) is no longer filled.
- URL list moved to `backend/src/recipe_urls.txt`; the scraper only runs when called directly.
- Added a simple web UI (`backend/src/web/`, Flask + waitress): browse/search/filter recipes,
  recipe detail page, add recipes by URL (via the scraper) or by hand, delete.
- Added Docker setup for this branch: one image, `bot` and `web` services, port 8099.
- Made the scraper more thorough (Mealie-style, own implementation since Mealie is AGPL):
  - `scraper_fetch.py`: blocks private/local addresses incl. redirects, rotates browser
    fingerprints on bot blocks, backoff on rate limits, timeouts and size cap.
  - `scraper_clean.py`: HTML/entity cleanup, HowToSections, ISO durations, yields, keywords.
  - New columns: recipe image, step sections, ingredient groups, original ingredient line.
    Added automatically to existing DBs. Web UI shows images, sections and groups.
- Added `recipe_lists/bbc_good_food_dinners.md` (225 dinner URLs in 10 categories; 67 from the
  original list, the rest unverified) and URL-list import: `--file` on the CLI and
  "Import from file" in the web UI (background job with progress page).
