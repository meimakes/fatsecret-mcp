"""Offline tests for FatSecret custom-food creation."""

import pytest

from fatsecret_mcp.client import FatSecretError
from fatsecret_mcp.server import _create_custom_food


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def call(self, method, params=None):
        self.calls.append((method, params or {}))
        if self.error:
            raise self.error
        return self.response


def test_create_custom_food_uses_v2_contract_and_all_supplied_nutrients():
    client = FakeClient({"food_id": {"value": "987654"}})

    result = _create_custom_food(
        client,
        name="Blueberry Lemon Thyme",
        brand="Everyday Carnivore",
        serving_size="1 bar",
        serving_amount=60,
        serving_amount_unit="g",
        calories=260,
        protein=10,
        fat=20,
        carbs=11,
        calories_from_fat=180,
        saturated_fat=8,
        polyunsaturated_fat=2,
        monounsaturated_fat=5,
        trans_fat=0,
        cholesterol=35,
        sodium=240,
        potassium=180,
        fiber=2,
        sugar=7,
        added_sugars=5,
        vitamin_d=1.5,
        vitamin_a=30,
        vitamin_c=2,
        calcium=80,
        iron=1.2,
    )

    assert result == {
        "created": True,
        "food_id": "987654",
        "food_name": "Blueberry Lemon Thyme",
        "brand_name": "Everyday Carnivore",
        "serving_size": "1 bar",
    }
    assert client.calls == [("food.create.v2", {
        "brand_type": "manufacturer",
        "brand_name": "Everyday Carnivore",
        "food_name": "Blueberry Lemon Thyme",
        "serving_size": "1 bar",
        "serving_amount": "60",
        "serving_amount_unit": "g",
        "calories": "260",
        "fat": "20",
        "carbohydrate": "11",
        "protein": "10",
        "calories_from_fat": "180",
        "saturated_fat": "8",
        "polyunsaturated_fat": "2",
        "monounsaturated_fat": "5",
        "trans_fat": "0",
        "cholesterol": "35",
        "sodium": "240",
        "potassium": "180",
        "fiber": "2",
        "sugar": "7",
        "added_sugars": "5",
        "vitamin_d": "1.5",
        "vitamin_a": "30",
        "vitamin_c": "2",
        "calcium": "80",
        "iron": "1.2",
    })]


def test_create_custom_food_returns_clear_premier_error():
    client = FakeClient(error=FatSecretError(10, "Unknown method", "food.create.v2"))

    result = _create_custom_food(client, name="Bar")

    assert result["created"] is False
    assert result["error"] == "premier_required"
    assert "Premier Exclusive" in result["message"]
    assert result["fatsecret_error"] == {"code": 10, "message": "Unknown method"}


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"name": " "}, "name must not be blank"),
        ({"name": "Bar", "serving_size": " "}, "serving_size must not be blank"),
        ({"name": "Bar", "brand_type": "store"}, "brand_type must be one of"),
        ({"name": "Bar", "calories": -1}, "calories must be a non-negative"),
        ({"name": "Bar", "sodium": float("nan")}, "sodium must be a non-negative"),
        ({"name": "Bar", "serving_amount": 0}, "serving_amount must be a positive"),
        (
            {"name": "Bar", "serving_amount": 1, "serving_amount_unit": "lb"},
            "serving_amount_unit must be one of",
        ),
    ],
)
def test_create_custom_food_validates_before_calling_api(kwargs, message):
    client = FakeClient({"food_id": {"value": "1"}})

    with pytest.raises(RuntimeError, match=message):
        _create_custom_food(client, **kwargs)

    assert client.calls == []


def test_create_custom_food_requires_food_id_in_success_response():
    client = FakeClient({"food_id": {}})

    with pytest.raises(RuntimeError, match="no food_id"):
        _create_custom_food(client, name="Bar")
