"""MCP tool surface (FastMCP, stdio transport).

Tools wrap the FS REST API with intuitive semantics — all the FS quirks
captured in notes here are transparently handled so callers don't trip on
them.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import Client, FatSecretError
from .config import Config

EPOCH = _dt.date(1970, 1, 1)
MAX_DIARY_RANGE_DAYS = 31

_MACRO_FIELDS = ("calories", "protein", "fat", "carbohydrate")
_NUTRIENT_FIELDS = (
    *_MACRO_FIELDS,
    "saturated_fat",
    "polyunsaturated_fat",
    "monounsaturated_fat",
    "cholesterol",
    "sodium",
    "potassium",
    "fiber",
    "sugar",
    "vitamin_a",
    "vitamin_c",
    "calcium",
    "iron",
)

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


def _as_list(value: Any) -> list[dict[str, Any]]:
    """Normalize FatSecret's historical singleton-or-array JSON shapes."""
    if not value:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    raise RuntimeError(f"unexpected FatSecret list value: {value!r}")


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nutrient_totals(nutrient_sets: list[dict[str, float | None]]) -> dict[str, float | None]:
    totals: dict[str, float | None] = {}
    for field in _NUTRIENT_FIELDS:
        values = [nutrients[field] for nutrients in nutrient_sets if nutrients[field] is not None]
        # Preserve the existing zero totals for the always-present core macros,
        # while optional nutrients remain null when FatSecret supplied no data.
        totals[field] = sum(values) if values else (0.0 if field in _MACRO_FIELDS else None)
    return totals


def _raw_or_cooked(*descriptions: Any) -> str | None:
    """Return the first explicit preparation state FatSecret supplied."""
    for description in descriptions:
        match = re.search(r"\b(raw|cooked|cooking)\b", str(description or ""), re.IGNORECASE)
        if match:
            return "raw" if match.group(1).lower() == "raw" else "cooked"
    return None


def _diary_entries(client: Client, date: _dt.date) -> list[dict[str, Any]]:
    """Fetch one day, normalizing FatSecret's error-1 empty-day response."""
    try:
        res = client.call("food_entries.get.v2", {"date": str((date - EPOCH).days)})
    except FatSecretError as e:
        if e.code == 1:
            return []
        raise
    return _as_list((res.get("food_entries") or {}).get("food_entry"))


def _serving_for_entry(
    client: Client,
    entry: dict[str, Any],
    food_cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Look up the exact serving referenced by a diary entry."""
    food_id = str(entry.get("food_id") or "")
    if food_id not in food_cache:
        food_cache[food_id] = client.call("food.get.v4", {"food_id": food_id}).get("food") or {}
    servings = _as_list((food_cache[food_id].get("servings") or {}).get("serving"))
    serving_id = str(entry.get("serving_id") or "")
    return next((s for s in servings if str(s.get("serving_id")) == serving_id), {})


def _enrich_diary_entry(
    client: Client,
    entry: dict[str, Any],
    food_cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    serving = _serving_for_entry(client, entry, food_cache)
    entry_units = _number(entry.get("number_of_units"))
    serving_units = _number(serving.get("number_of_units"))
    metric_serving_amount = serving.get("metric_serving_amount")
    metric_amount = None
    if metric_serving_amount not in (None, "") and serving_units > 0:
        metric_amount = _number(metric_serving_amount) * entry_units / serving_units

    measurement = serving.get("measurement_description")
    nutrients = {field: _optional_number(entry.get(field)) for field in _NUTRIENT_FIELDS}
    macros = {field: nutrients[field] for field in _MACRO_FIELDS}
    return {
        "food_entry_id": str(entry.get("food_entry_id") or ""),
        "date": (EPOCH + _dt.timedelta(days=int(entry.get("date_int") or 0))).isoformat(),
        "meal": entry.get("meal") or "Other",
        "food_id": str(entry.get("food_id") or ""),
        "serving_id": str(entry.get("serving_id") or ""),
        "number_of_units": entry_units,
        "original_amount": entry_units,
        "original_unit": measurement,
        "food_entry_description": entry.get("food_entry_description"),
        "serving_description": serving.get("serving_description"),
        "measurement_description": measurement,
        "metric_serving_amount": (
            _number(metric_serving_amount) if metric_serving_amount not in (None, "") else None
        ),
        "metric_serving_unit": serving.get("metric_serving_unit"),
        "metric_amount": metric_amount,
        "raw_or_cooked": _raw_or_cooked(
            measurement,
            serving.get("serving_description"),
            entry.get("food_entry_description"),
            entry.get("food_entry_name"),
        ),
        "food_entry_name": entry.get("food_entry_name") or "",
        **nutrients,
        "macros": macros,
        "nutrients": nutrients,
    }


def _day_diary(
    client: Client,
    date: _dt.date,
    food_cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    food_cache = food_cache if food_cache is not None else {}
    entries = [_enrich_diary_entry(client, entry, food_cache) for entry in _diary_entries(client, date)]
    totals = _nutrient_totals([entry["nutrients"] for entry in entries])
    return {"date": date.isoformat(), "entries": entries, "totals": totals}


def _diary_range(client: Client, start: _dt.date, end: _dt.date) -> dict[str, Any]:
    if end < start:
        raise RuntimeError("end_date must be on or after start_date")
    day_count = (end - start).days + 1
    if day_count > MAX_DIARY_RANGE_DAYS:
        raise RuntimeError(f"date range may not exceed {MAX_DIARY_RANGE_DAYS} days")

    food_cache: dict[str, dict[str, Any]] = {}
    days = [
        _day_diary(client, start + _dt.timedelta(days=offset), food_cache)
        for offset in range(day_count)
    ]
    totals = _nutrient_totals([day["totals"] for day in days])
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "days": days,
        "totals": totals,
    }


def _replace_entry(
    client: Client,
    food_entry_id: str,
    serving_id: str,
    number_of_units: float,
    meal: str = "",
    food_entry_name: str = "",
) -> dict[str, Any]:
    units = float(number_of_units)
    if not math.isfinite(units) or units <= 0:
        raise RuntimeError("number_of_units must be a positive finite number")

    params = {
        "food_entry_id": str(food_entry_id),
        "serving_id": str(serving_id),
        "number_of_units": f"{units:.4f}".rstrip("0").rstrip("."),
    }
    if meal:
        meal_key = MEAL_NORMALIZE.get(meal.lower())
        if not meal_key:
            raise RuntimeError(f"invalid meal: {meal!r}. Use Breakfast/Lunch/Dinner/Other (snack→Other).")
        params["meal"] = meal_key
    if food_entry_name:
        params["food_entry_name"] = food_entry_name

    res = client.call("food_entry.edit", params)
    success = res.get("success")
    success_value = success.get("value") if isinstance(success, dict) else success
    if str(success_value) != "1":
        raise RuntimeError(f"FS did not confirm food_entry.edit success: {res}")
    return {
        "replaced": True,
        "food_entry_id": str(food_entry_id),
        "serving_id": str(serving_id),
        "number_of_units": units,
        **({"meal": params["meal"]} if "meal" in params else {}),
        **({"food_entry_name": food_entry_name} if food_entry_name else {}),
    }


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
        """Get one day's diary as structured JSON (YYYY-MM-DD, default today).

        Every entry includes its food/serving IDs, exact FatSecret
        number_of_units, original amount and unit, serving and measurement
        descriptions, metric serving and scaled metric amounts, explicit
        raw/cooked designation when present, entry name, and every nutrient
        FatSecret supplied for the diary entry.
        """
        day = _dt.date.fromisoformat(date) if date else _dt.date.today()
        return json.dumps(_day_diary(client, day), indent=2)

    @mcp.tool()
    def get_diary_range(start_date: str, end_date: str) -> str:
        """Get an inclusive date range of enriched diary entries as JSON.

        Dates are YYYY-MM-DD. The range is limited to 31 days to keep the
        upstream request count bounded. Serving lookups are cached across the
        range, so each distinct food is fetched only once.
        """
        start = _dt.date.fromisoformat(start_date)
        end = _dt.date.fromisoformat(end_date)
        return json.dumps(_diary_range(client, start, end), indent=2)

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
    def replace_entry(
        food_entry_id: str,
        serving_id: str,
        number_of_units: float,
        meal: str = "",
        food_entry_name: str = "",
    ) -> str:
        """Atomically replace an entry's serving and amount via food_entry.edit.

        `number_of_units` uses FatSecret's native absolute-unit semantics; copy
        the current value from get_diary when only changing the serving. Meal
        and name are changed in the same upstream operation when supplied.

        FatSecret does not allow an edit to change food_id or date. To change
        either, create a new entry and delete the old one (which cannot be made
        atomic by this API).
        """
        return json.dumps(_replace_entry(
            client,
            food_entry_id=food_entry_id,
            serving_id=serving_id,
            number_of_units=number_of_units,
            meal=meal,
            food_entry_name=food_entry_name,
        ), indent=2)

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
