"""MCP tool surface (FastMCP, stdio transport).

Tools wrap the FS REST API with intuitive semantics — all the FS quirks
captured in notes here are transparently handled so callers don't trip on
them.
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import Client, FatSecretError
from .config import Config

EPOCH = _dt.date(1970, 1, 1)

# FS-valid meal values. App also has "Snack" in its UI but the API rejects it;
# snack entries must be logged as "Other". We normalize.
MEAL_NORMALIZE = {
    "breakfast": "Breakfast",
    "lunch": "Lunch",
    "dinner": "Dinner",
    "other": "Other",
    "snack": "Other",
    "snacks": "Other",
}

# Unit conversions → grams (weight) or ml (volume). Covers the common weight
# units callers want. Volume units are approximated as grams for solid foods;
# accurate only for water-density liquids. `log_amount` falls back to the
# food's metric gram serving, so grams are always the safe path.
_WEIGHT_TO_G = {
    "g": 1.0,
    "gram": 1.0,
    "grams": 1.0,
    "oz": 28.3495,
    "ounce": 28.3495,
    "ounces": 28.3495,
    "lb": 453.592,
    "lbs": 453.592,
    "pound": 453.592,
    "pounds": 453.592,
    "kg": 1000.0,
}
_VOLUME_TO_ML = {
    "ml": 1.0,
    "l": 1000.0,
    "liter": 1000.0,
    "liters": 1000.0,
    "floz": 29.5735,
    "fl_oz": 29.5735,
    "fluid_ounce": 29.5735,
    "tbsp": 14.7868,
    "tablespoon": 14.7868,
    "tablespoons": 14.7868,
    "tsp": 4.92892,
    "teaspoon": 4.92892,
    "teaspoons": 4.92892,
    "cup": 236.588,
    "cups": 236.588,
}


def build_server() -> FastMCP:
    cfg = Config.load()
    if cfg.user_token is None:
        raise RuntimeError(
            "No user token configured — the diary tools need 3-legged OAuth. "
            "Run `fatsecret-mcp auth` once to authorize, then re-start the server."
        )
    client = Client(consumer=cfg.consumer, token=cfg.user_token)
    mcp = FastMCP("fatsecret")
    _register_tools(mcp, client)
    return mcp


def _date_int(date_str: str = "") -> int:
    d = _dt.date.fromisoformat(date_str) if date_str else _dt.date.today()
    return (d - EPOCH).days


def _register_tools(mcp: FastMCP, client: Client) -> None:
    # ---- public food DB ----------------------------------------------------

    @mcp.tool()
    def search_food(query: str, max_results: int = 10) -> str:
        """Search FatSecret's public food database by name/brand."""
        max_results = max(1, min(50, int(max_results)))
        res = client.call("foods.search", {"search_expression": query, "max_results": str(max_results)})
        foods = (res.get("foods") or {}).get("food") or []
        if isinstance(foods, dict):
            foods = [foods]
        if not foods:
            return f"no results for: {query}"
        lines = []
        for f in foods:
            tag = f" [{f['brand_name']}]" if f.get("brand_name") else ""
            lines.append(f"- [{f.get('food_id')}] {f.get('food_name')}{tag}  {f.get('food_description', '')}")
        return "\n".join(lines)

    @mcp.tool()
    def get_food(food_id: str) -> str:
        """Full macros + every available serving (with serving_id) for a food."""
        res = client.call("food.get.v4", {"food_id": str(food_id)})
        food = res.get("food")
        if not food:
            return f"food not found: {food_id}"
        name = food.get("food_name", "")
        brand = food.get("brand_name", "")
        servings = (food.get("servings") or {}).get("serving") or []
        if isinstance(servings, dict):
            servings = [servings]
        header = f"{name}" + (f" [{brand}]" if brand else "")
        lines = [header]
        for s in servings[:20]:
            lines.append(
                f"  [serving_id {s.get('serving_id')}] {s.get('serving_description', '')}: "
                f"{s.get('calories', '?')} cal, P{s.get('protein', '?')} F{s.get('fat', '?')} C{s.get('carbohydrate', '?')}"
            )
        return "\n".join(lines)

    # ---- user diary --------------------------------------------------------

    @mcp.tool()
    def get_profile() -> str:
        """Get the authenticated user's FS profile (height, weight, goal)."""
        res = client.call("profile.get")
        return json.dumps(res.get("profile", {}), indent=2)

    @mcp.tool()
    def get_diary(date: str = "") -> str:
        """Diary entries for a date (YYYY-MM-DD, default today), grouped by meal."""
        d_int = _date_int(date)
        # FS quirk: `food_entries.get.v2` returns error 1 ("unknown error, try again later")
        # when there are zero entries for the date, instead of an empty list. Treat that
        # specific code as "no entries" rather than propagating the error.
        try:
            res = client.call("food_entries.get.v2", {"date": str(d_int)})
        except FatSecretError as e:
            if e.code == 1:
                return f"no entries for {date or 'today'}"
            raise
        entries = (res.get("food_entries") or {}).get("food_entry") or []
        if isinstance(entries, dict):
            entries = [entries]
        if not entries:
            return f"no entries for {date or 'today'}"
        by_meal: dict[str, list[str]] = {}
        totals = {"cal": 0.0, "p": 0.0, "f": 0.0, "c": 0.0}
        for e in entries:
            meal = e.get("meal", "Other")
            cal = float(e.get("calories", 0) or 0)
            p = float(e.get("protein", 0) or 0)
            f_ = float(e.get("fat", 0) or 0)
            c = float(e.get("carbohydrate", 0) or 0)
            totals["cal"] += cal; totals["p"] += p; totals["f"] += f_; totals["c"] += c
            by_meal.setdefault(meal, []).append(
                f"  [{e.get('food_entry_id')}] {e.get('food_entry_name')} — "
                f"{cal:.0f} cal, P{p:.1f} F{f_:.1f} C{c:.1f}"
            )
        out = [f"Diary {date or 'today'}:"]
        for meal in ("Breakfast", "Lunch", "Dinner", "Other"):
            if meal in by_meal:
                out.append(f"{meal}:")
                out.extend(by_meal[meal])
        out.append(f"TOTAL: {totals['cal']:.0f} cal, P{totals['p']:.1f} F{totals['f']:.1f} C{totals['c']:.1f}")
        return "\n".join(out)

    @mcp.tool()
    def log_food(
        food_id: str,
        serving_id: str,
        servings: float,
        meal: str = "Breakfast",
        date: str = "",
        food_entry_name: str = "",
    ) -> str:
        """Log a food to the user's diary.

        `servings` is an intuitive multiplier of the named serving — e.g.
        2 for "2 tbsp", 0.5 for "half a stick". The MCP translates that to
        FS's `number_of_units` semantics internally.

        FS gotcha (handled here): the API's `number_of_units` is in the
        serving's own measurement units (grams for a "100 g" serving, tbsp
        for "1 tbsp", etc.), NOT a multiplier. Each serving carries its own
        `number_of_units` describing how many measurement-units equal one
        whole serving. We multiply caller's `servings` by that to produce
        the correct API value.

        Meal: Breakfast | Lunch | Dinner | Other. FS rejects "Snack" — we
        map it to "Other" automatically.
        """
        meal_key = MEAL_NORMALIZE.get(meal.lower())
        if not meal_key:
            raise RuntimeError(f"invalid meal: {meal!r}. Use Breakfast/Lunch/Dinner/Other (snack→Other).")

        info = client.call("food.get.v4", {"food_id": str(food_id)}).get("food") or {}
        if not food_entry_name:
            food_entry_name = info.get("food_name") or f"food {food_id}"
        servings_list = (info.get("servings") or {}).get("serving") or []
        if isinstance(servings_list, dict):
            servings_list = [servings_list]
        serving = next((s for s in servings_list if str(s.get("serving_id")) == str(serving_id)), None)
        if not serving:
            raise RuntimeError(f"serving_id {serving_id} not found on food {food_id}")
        serving_units = float(serving.get("number_of_units") or 1)
        serving_desc = serving.get("serving_description") or "?"
        api_units = float(servings) * serving_units

        res = client.call("food_entry.create", {
            "food_id": str(food_id),
            "food_entry_name": food_entry_name,
            "serving_id": str(serving_id),
            "number_of_units": f"{api_units:.4f}".rstrip("0").rstrip("."),
            "meal": meal_key,
            "date": str(_date_int(date)),
        })
        fe = res.get("food_entry_id")
        fe_id = fe.get("value") if isinstance(fe, dict) else fe
        if not fe_id:
            raise RuntimeError(f"FS returned no food_entry_id — unexpected response: {res}")
        return (
            f"logged (food_entry_id={fe_id}) {servings}× '{serving_desc}' of "
            f"{food_entry_name} to {meal_key} on {date or 'today'} "
            f"(sent number_of_units={api_units})"
        )

    @mcp.tool()
    def log_amount(
        food_id: str,
        amount: float,
        unit: str = "g",
        meal: str = "Breakfast",
        date: str = "",
        food_entry_name: str = "",
    ) -> str:
        """Log a food by absolute amount + unit — no need to pre-pick a serving.

        amount: how much (2.5, 100, etc.)
        unit:   g | oz | lb | kg  (weight)
                ml | fl_oz | tbsp | tsp | cup  (volume; approximate for solids)
        meal:   Breakfast | Lunch | Dinner | Other (snack → Other)
        date:   YYYY-MM-DD, default today

        How it picks the serving:
          1. If the food has a named serving matching `unit` exactly (e.g.
             "1 oz" when unit=oz), use that with number_of_units=amount —
             FS renders natively as "N oz".
          2. Otherwise, convert `amount` to grams (or ml → grams for volume)
             and use the metric gram serving with number_of_units=grams —
             FS renders as "N g".

        Preferred over `log_food` when the caller knows amount in absolute
        units rather than "multiples of some specific serving." For named
        portions ("1 cup", "half a stick") use `log_food` with the explicit
        serving_id from `get_food`.
        """
        meal_key = MEAL_NORMALIZE.get(meal.lower())
        if not meal_key:
            raise RuntimeError(f"invalid meal: {meal!r}. Use Breakfast/Lunch/Dinner/Other (snack→Other).")

        unit_norm = unit.lower().strip().replace(" ", "_")
        if unit_norm in _WEIGHT_TO_G:
            amount_g = float(amount) * _WEIGHT_TO_G[unit_norm]
        elif unit_norm in _VOLUME_TO_ML:
            # Treat ml as grams for non-dense-liquid foods. Good enough for
            # carnivore / whole-food logging. Dairy/oil will be ~5-10% off.
            amount_g = float(amount) * _VOLUME_TO_ML[unit_norm]
        else:
            raise RuntimeError(
                f"unknown unit: {unit!r}. Supported: "
                f"{', '.join(sorted(set(_WEIGHT_TO_G) | set(_VOLUME_TO_ML)))}"
            )

        info = client.call("food.get.v4", {"food_id": str(food_id)}).get("food") or {}
        if not food_entry_name:
            food_entry_name = info.get("food_name") or f"food {food_id}"
        servings_list = (info.get("servings") or {}).get("serving") or []
        if isinstance(servings_list, dict):
            servings_list = [servings_list]
        if not servings_list:
            raise RuntimeError(f"food {food_id} has no servings defined")

        # Strategy:
        # 1. If the food has a serving whose measurement_description starts
        #    with the caller's unit (e.g. "oz" matches "oz, boneless, raw"),
        #    use it directly — FS renders natively as "N <unit>".
        # 2. Otherwise compute via grams. Every FS serving carries
        #    metric_serving_amount + metric_serving_unit (usually g or oz).
        #    Convert serving's metric to grams, derive grams-per-unit, and
        #    compute api_number_of_units = desired_grams / grams_per_unit.
        #    Works for "1 serving (85g)", "1 package (100g)", "1 jar (28g)",
        #    and FS-encoded-in-oz servings like Whole Foods prepared items.
        named_match = None
        for s in servings_list:
            m = (s.get("measurement_description") or "").lower()
            first_token = m.split(",")[0].split()[0] if m else ""
            if first_token == unit_norm or first_token == unit_norm.rstrip("s"):
                if float(s.get("number_of_units") or 0) == 1.0:
                    named_match = s
                    break
                if named_match is None:
                    named_match = s

        if named_match:
            chosen = named_match
            api_units = float(amount)
            how = f"{amount} {unit_norm}"
        else:
            # Pick any serving with usable metric info; prefer ones with
            # smaller metric_serving_amount (finer-grained granularity).
            usable = []
            for s in servings_list:
                mu = (s.get("metric_serving_unit") or "").lower()
                try:
                    msa = float(s.get("metric_serving_amount") or 0)
                    nu = float(s.get("number_of_units") or 0)
                except ValueError:
                    continue
                if msa <= 0 or nu <= 0:
                    continue
                if mu == "g":
                    grams_per_serving_unit = msa / nu
                elif mu == "oz":
                    grams_per_serving_unit = msa * 28.3495 / nu
                elif mu == "ml":
                    grams_per_serving_unit = msa / nu  # approx
                else:
                    continue
                usable.append((grams_per_serving_unit, s))
            if not usable:
                raise RuntimeError(
                    f"food {food_id} has no servings with usable metric info. "
                    f"Call get_food({food_id}) + log_food with an explicit serving_id."
                )
            usable.sort()  # smallest grams-per-unit first → best precision
            grams_per_unit, chosen = usable[0]
            api_units = amount_g / grams_per_unit
            how = (
                f"{amount_g:.2f} g (from {amount} {unit_norm}) → "
                f"{api_units:.3f}× '{chosen.get('serving_description')}'"
            )

        res = client.call("food_entry.create", {
            "food_id": str(food_id),
            "food_entry_name": food_entry_name,
            "serving_id": str(chosen["serving_id"]),
            "number_of_units": f"{api_units:.4f}".rstrip("0").rstrip("."),
            "meal": meal_key,
            "date": str(_date_int(date)),
        })
        fe = res.get("food_entry_id")
        fe_id = fe.get("value") if isinstance(fe, dict) else fe
        if not fe_id:
            raise RuntimeError(f"FS returned no food_entry_id — unexpected response: {res}")
        return (
            f"logged (food_entry_id={fe_id}) {how} of {food_entry_name} "
            f"to {meal_key} on {date or 'today'} "
            f"(via serving '{chosen.get('serving_description')}', number_of_units={api_units})"
        )

    @mcp.tool()
    def delete_entry(food_entry_id: str) -> str:
        """Delete a diary entry by food_entry_id (from get_diary)."""
        client.call("food_entry.delete", {"food_entry_id": str(food_entry_id)})
        return f"deleted entry {food_entry_id}"

    @mcp.tool()
    def create_custom_food(
        name: str, brand: str = "",
        calories: float = 0, protein: float = 0, fat: float = 0, carbs: float = 0,
    ) -> str:
        """Create a custom food with per-100g macros.

        PREMIER-ONLY on FatSecret's platform tier. Free-tier apps will
        receive 'invalid_scope' or similar. Upgrade the app in the FS dev
        console if you need custom foods.
        """
        try:
            res = client.call("foods.create", {
                "food_name": name,
                "brand_name": brand or "",
                "calories": str(calories),
                "protein": str(protein),
                "fat": str(fat),
                "carbohydrate": str(carbs),
            })
        except FatSecretError as e:
            if "scope" in e.message.lower() or "premier" in e.message.lower():
                return f"create_custom_food requires FS premier tier. Current error: {e}"
            raise
        return json.dumps(res, indent=2)
