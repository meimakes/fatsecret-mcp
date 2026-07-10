"""Offline tests for enriched diary reads and atomic entry replacement."""

import datetime as dt

import pytest

from fatsecret_mcp.client import FatSecretError
from fatsecret_mcp.server import EPOCH, _day_diary, _diary_range, _replace_entry


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call(self, method, params=None):
        params = params or {}
        self.calls.append((method, params))
        response = self.responses[method]
        return response(params) if callable(response) else response


def _entry(date: dt.date, entry_id: str = "entry-1"):
    return {
        "food_entry_id": entry_id,
        "food_entry_description": "9 oz boneless, raw chicken breast",
        "food_entry_name": "Chicken breast",
        "date_int": str((date - EPOCH).days),
        "meal": "Dinner",
        "food_id": "1641",
        "serving_id": "5041",
        "number_of_units": "9.000",
        "calories": "315",
        "protein": "47.88",
        "fat": "12.51",
        "carbohydrate": "0",
    }


FOOD_RESPONSE = {
    "food": {
        "food_id": "1641",
        "servings": {
            "serving": [
                {
                    "serving_id": "5041",
                    "serving_description": "1 oz boneless (yield after cooking)",
                    "number_of_units": "1.000",
                    "measurement_description": "oz, boneless, raw (yield after cooking)",
                    "metric_serving_amount": "18.000",
                    "metric_serving_unit": "g",
                }
            ]
        },
    }
}


def test_day_diary_returns_complete_enriched_entry():
    date = dt.date(2026, 7, 9)
    client = FakeClient({
        "food_entries.get.v2": {"food_entries": {"food_entry": _entry(date)}},
        "food.get.v4": FOOD_RESPONSE,
    })

    result = _day_diary(client, date)

    assert result["date"] == "2026-07-09"
    assert result["totals"] == {
        "calories": 315.0,
        "protein": 47.88,
        "fat": 12.51,
        "carbohydrate": 0.0,
    }
    entry = result["entries"][0]
    assert entry["food_id"] == "1641"
    assert entry["serving_id"] == "5041"
    assert entry["number_of_units"] == 9.0
    assert entry["original_amount"] == 9.0
    assert entry["original_unit"] == "oz, boneless, raw (yield after cooking)"
    assert entry["measurement_description"] == entry["original_unit"]
    assert entry["metric_serving_amount"] == 18.0
    assert entry["metric_serving_unit"] == "g"
    assert entry["metric_amount"] == 162.0
    assert entry["raw_or_cooked"] == "raw"
    assert entry["food_entry_name"] == "Chicken breast"
    assert entry["calories"] == 315.0
    assert entry["protein"] == 47.88
    assert entry["fat"] == 12.51
    assert entry["carbohydrate"] == 0.0
    assert entry["macros"] == result["totals"]


def test_diary_range_is_inclusive_and_caches_food_lookup():
    start = dt.date(2026, 7, 8)
    end = dt.date(2026, 7, 9)

    def entries_response(params):
        date = EPOCH + dt.timedelta(days=int(params["date"]))
        return {"food_entries": {"food_entry": [_entry(date, f"entry-{date.day}")]}}

    client = FakeClient({
        "food_entries.get.v2": entries_response,
        "food.get.v4": FOOD_RESPONSE,
    })

    result = _diary_range(client, start, end)

    assert [day["date"] for day in result["days"]] == ["2026-07-08", "2026-07-09"]
    assert result["totals"]["calories"] == 630.0
    assert [method for method, _ in client.calls].count("food_entries.get.v2") == 2
    assert [method for method, _ in client.calls].count("food.get.v4") == 1


def test_diary_empty_error_is_an_empty_structured_day():
    date = dt.date(2026, 7, 9)

    def no_entries(_params):
        raise FatSecretError(1, "unknown error, try again later")

    client = FakeClient({"food_entries.get.v2": no_entries})

    assert _day_diary(client, date) == {
        "date": "2026-07-09",
        "entries": [],
        "totals": {"calories": 0, "protein": 0, "fat": 0, "carbohydrate": 0},
    }


def test_diary_range_validates_order_and_length():
    client = FakeClient({})
    with pytest.raises(RuntimeError, match="on or after"):
        _diary_range(client, dt.date(2026, 7, 9), dt.date(2026, 7, 8))
    with pytest.raises(RuntimeError, match="31 days"):
        _diary_range(client, dt.date(2026, 1, 1), dt.date(2026, 2, 1))


def test_replace_entry_uses_one_native_edit_call():
    client = FakeClient({"food_entry.edit": {"success": {"value": "1"}}})

    result = _replace_entry(
        client,
        food_entry_id="123",
        serving_id="5041",
        number_of_units=6.5,
        meal="snack",
        food_entry_name="Chicken",
    )

    assert result == {
        "replaced": True,
        "food_entry_id": "123",
        "serving_id": "5041",
        "number_of_units": 6.5,
        "meal": "Other",
        "food_entry_name": "Chicken",
    }
    assert client.calls == [("food_entry.edit", {
        "food_entry_id": "123",
        "serving_id": "5041",
        "number_of_units": "6.5",
        "meal": "Other",
        "food_entry_name": "Chicken",
    })]


def test_replace_entry_rejects_invalid_units_without_calling_api():
    client = FakeClient({})
    with pytest.raises(RuntimeError, match="positive finite"):
        _replace_entry(client, "123", "5041", float("nan"))
    assert client.calls == []
