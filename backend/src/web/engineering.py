"""Build a "Cooking for Engineers"-style recipe table.

Ingredients go down the left column. Each step is a cell to the right of
everything it combines, spanning those rows, so the recipe reads as a tree from
left to right and the last step fills the last column.

The tree comes from the recipe's step graph (recipe_steps.next_step_id) when the
DB has one. Otherwise it is derived: steps in the same section form a chain, and
the first step of the last section combines the results of every earlier
section; without sections the steps form one chain. Each ingredient feeds the
step it is first used in (recipe_ingredients.step_id), or the last step if that
is unknown. A prep note ("chopped") becomes its own small operation.
"""

import re

PREHEAT_RE = re.compile(r"\b(?:pre-?)?heat(?:\s+the)?\s+(oven|grill)\s+to\s+([^.;,]+(?:\([^)]*\))?)",
                        re.IGNORECASE)
LABEL_MAX = 70


def _short(text: str, limit: int = LABEL_MAX) -> str:
    """First sentence, cut at a word boundary if it's still long."""
    first = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0].rstrip(".")
    if len(first) <= limit:
        return first
    return first[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _banner(steps: list[dict]) -> str | None:
    for step in steps:
        if match := PREHEAT_RE.search(step["instruction"]):
            target = re.split(r"\s+(?:and|then|while)\s+", match.group(2).strip())[0]
            if len(target) > 60:
                target = target[:60].rsplit(" ", 1)[0] + "…"
            return f"Preheat {match.group(1).lower()} to {target}"
    return None


def _step_parents(steps: list[dict]) -> dict[int, int | None]:
    """step_id -> step_id it feeds into (None for the final step)."""
    by_id = {s["step_id"]: s for s in steps}
    last = steps[-1]["step_id"]
    parents: dict[int, int | None] = {}

    if any(s.get("next_step_id") for s in steps):
        for s in steps:
            nxt = by_id.get(s.get("next_step_id"))
            ok = nxt is not None and nxt["step_number"] > s["step_number"]
            parents[s["step_id"]] = nxt["step_id"] if ok else (None if s["step_id"] == last else last)
        return parents

    # Group consecutive steps by section; each group is a chain.
    groups: list[list[dict]] = []
    for s in steps:
        if groups and groups[-1][0].get("section") == s.get("section"):
            groups[-1].append(s)
        else:
            groups.append([s])
    for group in groups:
        for a, b in zip(group, group[1:]):
            parents[a["step_id"]] = b["step_id"]
    merge_into = groups[-1][0]["step_id"]
    for group in groups[:-1]:
        parents[group[-1]["step_id"]] = merge_into
    parents[last] = None
    return parents


def build_table(steps: list[dict], ingredients: list[dict], format_qty) -> dict | None:
    """Return {"columns", "rows", "banner"} for the template, or None if there are no steps.

    steps: step_id, step_number, instruction, duration_text, section, next_step_id
    ingredients: name, amount, unit, prep_method, step_id, original_text (in display order)
    format_qty(amount, unit) -> str
    """
    if not steps:
        return None
    steps = sorted(steps, key=lambda s: s["step_number"])
    step_ids = {s["step_id"] for s in steps}
    last = steps[-1]["step_id"]
    parents = _step_parents(steps)

    # --- tree -------------------------------------------------------------
    nodes = {s["step_id"]: {"kind": "step", "step": s, "children": []} for s in steps}
    for s in steps:  # child steps first, in step order
        parent = parents.get(s["step_id"])
        if parent is not None:
            nodes[parent]["children"].append(nodes[s["step_id"]])
    for ing in ingredients:
        target = ing["step_id"] if ing["step_id"] in step_ids else last
        qty = format_qty(ing["amount"], ing["unit"])
        leaf = {"kind": "ingredient", "children": [],
                "text": f"{qty} {ing['name']}".strip(), "full": ing.get("original_text") or ""}
        if ing.get("prep_method"):
            leaf = {"kind": "prep", "children": [leaf], "text": ing["prep_method"]}
        nodes[target]["children"].append(leaf)
    root = nodes[last]

    # --- layout -----------------------------------------------------------
    def measure(node) -> None:
        if not node["children"]:  # a step with nothing to combine still needs a row
            if node["kind"] == "step":
                node["children"].append({"kind": "empty", "children": []})
        for child in node["children"]:
            measure(child)
        node["height"] = 1 + max((c["height"] for c in node["children"]), default=-1)
        node["leaves"] = sum(c["leaves"] for c in node["children"]) or 1

    measure(root)
    columns = root["height"] + 1
    rows: list[list[dict]] = [[] for _ in range(root["leaves"])]

    def place(node, row: int) -> None:
        col = node["height"]
        cell = {"col": col, "rowspan": node["leaves"], "colspan": 1, "kind": node["kind"]}
        if node["kind"] == "step":
            s = node["step"]
            cell.update(text=_short(s["instruction"]), full=s["instruction"],
                        number=s["step_number"], duration=s.get("duration_text"))
        elif node["kind"] in ("ingredient", "prep"):
            cell.update(text=node["text"], full=node.get("full", ""))
        rows[row].append(cell)

        offset = row
        blank = None  # merge blank space of neighbouring children that end in the same column
        for child in node["children"]:
            place(child, offset)
            gap_start = child["height"] + 1
            if gap_start < col:
                if blank and blank["col"] == gap_start and blank["end"] == offset:
                    blank["rowspan"] += child["leaves"]
                else:
                    blank = {"col": gap_start, "rowspan": child["leaves"],
                             "colspan": col - gap_start, "kind": "blank"}
                    rows[offset].append(blank)
                blank["end"] = offset + child["leaves"]
            else:
                blank = None
            offset += child["leaves"]

    place(root, 0)
    for r in rows:
        r.sort(key=lambda c: c["col"])
    return {"columns": columns, "rows": rows, "banner": _banner(steps)}
