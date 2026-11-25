"""Turn messy schema.org recipe data into clean, predictable Python values.

Covers the quirks real sites have (inspired by Mealie's cleaner, independently
implemented): HTML and double-escaped entities in text, instructions grouped in
HowToSections or given as one string / a JSON string / an index-keyed dict,
ingredients wrapped in text nodes, ISO 8601 or plain-number durations, yields
given as lists or ranges, and comma-separated or list-valued keywords.
"""

import html
import json
import re

TAG_RE = re.compile(r"<[^<]+?>")
SPACES_RE = re.compile(r"[ \t ]+")
BLANK_LINES_RE = re.compile(r"\n\s*\n+")
ISO_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+(?:\.\d+)?)D)?"
    r"(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?(?:(?P<minutes>\d+(?:\.\d+)?)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
SERVING_WORDS = ("serve", "serving", "portion", "people", "person", "makes", "yield")
MAX_MINUTES = 7 * 24 * 60


def clean_text(value) -> str:
    """Strip HTML tags and entities (repeatedly, for double-escaped sites) and tidy whitespace."""
    if isinstance(value, list):
        value = value[0] if value else ""
    if value is None:
        return ""
    text = str(value)
    for _ in range(3):  # "&amp;lt;b&amp;gt;" needs more than one pass
        new = SPACES_RE.sub(" ", TAG_RE.sub(" ", html.unescape(text)))
        if new == text:
            break
        text = new
    text = BLANK_LINES_RE.sub("\n", text.replace("\r", ""))
    return "\n".join(line.strip() for line in text.split("\n")).strip()


# --------------------------------------------------------------------------- #
# Instructions
# --------------------------------------------------------------------------- #

def _is_section(entry) -> bool:
    return isinstance(entry, dict) and "HowToSection" in (entry.get("@type"), entry.get("type"))


def _step_text(entry) -> str:
    if isinstance(entry, dict):
        entry = entry.get("text") or entry.get("name") or ""
    return clean_text(entry)


def parse_instructions(raw) -> list[dict]:
    """Return [{"text": ..., "section": ... or None}, ...] from any common shape."""
    if not raw:
        return []

    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped.startswith(("[", "{")):
            try:
                return parse_instructions(json.loads(stripped))
            except json.JSONDecodeError:
                pass
        return [{"text": t, "section": None}
                for t in (clean_text(line) for line in stripped.splitlines()) if t]

    if isinstance(raw, dict):
        if _is_section(raw) or "text" in raw:
            return parse_instructions([raw])
        # {"0": {...}, "1": {...}} or {1: {...}}
        keys = sorted(raw, key=lambda k: int(k) if str(k).isdigit() else 0)
        return parse_instructions([raw[k] for k in keys])

    if not isinstance(raw, list):
        return []

    steps = []
    for entry in raw:
        if _is_section(entry):
            section = clean_text(entry.get("name") or entry.get("Name")) or None
            items = entry.get("itemListElement", entry.get("item", []))
            for sub in parse_instructions(items):
                steps.append({"text": sub["text"], "section": section or sub["section"]})
        elif isinstance(entry, (str, dict)):
            if isinstance(entry, str) and "\n" in entry.strip():
                steps.extend(parse_instructions(entry))
            elif text := _step_text(entry):
                steps.append({"text": text, "section": None})
    return steps


def _comparable(text: str) -> str:
    return " ".join(text.split()).casefold()


def choose_instructions(structured: list[dict], flat: list[str]) -> list[dict]:
    """Prefer the page's structured steps only when they add section headings and
    lose none of the text the site-specific scraper found; otherwise use the flat list."""
    flat_steps = [{"text": clean_text(t), "section": None} for t in flat if clean_text(t)]
    if not structured:
        return flat_steps
    if not flat_steps:
        return structured
    if not any(s["section"] for s in structured):
        return flat_steps
    covered = {_comparable(s["text"]) for s in structured}
    covered |= {_comparable(s["section"]) for s in structured if s["section"]}
    if all(_comparable(s["text"]) in covered for s in flat_steps):
        return structured
    return flat_steps


# --------------------------------------------------------------------------- #
# Ingredients
# --------------------------------------------------------------------------- #

def clean_ingredient_lines(raw) -> list[str]:
    """Flatten ingredient data to clean text lines (handles text nodes and newline strings)."""
    if not raw:
        return []
    if isinstance(raw, str):
        raw = raw.splitlines()
    lines = []
    for item in raw if isinstance(raw, list) else [raw]:
        if isinstance(item, dict):
            item = item.get("textValue") or item.get("text") or item.get("name") or ""
        text = clean_text(item)
        if text:
            lines.append(text)
    return lines


def ingredient_groups(groups, fallback_lines: list[str]) -> list[tuple[str | None, list[str]]]:
    """[(group name or None, [lines]), ...]; falls back to one unnamed group."""
    result = []
    for group in groups or []:
        lines = clean_ingredient_lines(getattr(group, "ingredients", None))
        if lines:
            purpose = clean_text(getattr(group, "purpose", None)) or None
            result.append((purpose, lines))
    if not result or sum(len(lines) for _, lines in result) < len(fallback_lines):
        return [(None, fallback_lines)] if fallback_lines else result
    return result


# --------------------------------------------------------------------------- #
# Times, yields, keywords, images
# --------------------------------------------------------------------------- #

def duration_minutes(value) -> int | None:
    """Minutes from "PT1H30M", 30, "30", {"minValue": ...} or a list; None if unusable."""
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, dict):
        return duration_minutes(value.get("minValue") or value.get("value"))
    if isinstance(value, list):
        return duration_minutes(value[0]) if value else None
    if isinstance(value, (int, float)):
        minutes = float(value)
    else:
        text = str(value).strip()
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            minutes = float(text)
        elif match := ISO_DURATION_RE.match(text):
            parts = {k: float(v) for k, v in match.groupdict().items() if v}
            if not parts:
                return None
            minutes = (parts.get("days", 0) * 1440 + parts.get("hours", 0) * 60
                       + parts.get("minutes", 0) + parts.get("seconds", 0) / 60)
        else:
            return None
    minutes = round(minutes)
    return minutes if 0 < minutes <= MAX_MINUTES else None


def servings(value) -> int:
    """Servings from "4 servings", "Serves 4-6", ["4", "4 pies"], 4 ... (first number of a range)."""
    candidates = value if isinstance(value, list) else [value]
    texts = [clean_text(v) for v in candidates if v not in (None, "")]
    if not texts:
        return 0
    preferred = [t for t in texts if any(w in t.lower() for w in SERVING_WORDS)]
    for text in preferred + texts:
        if match := NUMBER_RE.search(text):
            number = int(float(match.group().replace(",", ".")))
            if 0 < number <= 100:
                return number
    return 0


def keyword_list(value) -> list[str]:
    """Lowercase, de-duplicated list from "a, b", ["a", "b"] or [{"name": "a"}]."""
    if not value or isinstance(value, (int, float)):
        return []
    items = value if isinstance(value, list) else str(value).split(",")
    result = []
    for item in items:
        if isinstance(item, dict):
            item = item.get("name", "")
        for part in str(item).split(","):
            text = clean_text(part).lower()
            if text and text not in result and len(text) <= 40:
                result.append(text)
    return result


def image_url(value) -> str | None:
    """First usable image URL from a string, list, or ImageObject."""
    if isinstance(value, list):
        for item in value:
            if url := image_url(item):
                return url
        return None
    if isinstance(value, dict):
        return image_url(value.get("url") or value.get("contentUrl"))
    if isinstance(value, str) and value.strip().startswith(("http://", "https://")):
        return value.strip()
    return None
