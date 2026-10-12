"""
A new size can arrive with its first stock in the same call, recorded as an
ordinary adjustment (batch + movement), so it is sellable the moment it is saved.
"""

import pytest

from apps.inventory.models import InventoryMovement

pytestmark = pytest.mark.django_db


@pytest.fixture
def product(product_with_variant_and_stock):
    return product_with_variant_and_stock["product"]


def _add(client, product, **extra):
    body = {"name_suffix": "نصف كيلو", "price": "180.00", "weight_grams": 500, **extra}
    return client.post(f"/api/v1/admin-api/products/{product.pk}/variants/", body, format="json")


def test_new_size_with_initial_stock_is_sellable(staff_client, product):
    response = _add(staff_client, product, initial_stock=12)
    assert response.status_code == 201
    variant = product.variants.get(pk=response.json()["data"]["id"])
    assert variant.inventory_item.available_quantity == 12
    movement = InventoryMovement.objects.get(item=variant.inventory_item)
    assert movement.quantity_delta == 12 and movement.notes == "" and movement.created_by is not None


def test_new_size_without_stock_still_gets_an_inventory_row(staff_client, product):
    response = _add(staff_client, product)
    variant = product.variants.get(pk=response.json()["data"]["id"])
    assert variant.inventory_item.available_quantity == 0
    assert not InventoryMovement.objects.filter(item=variant.inventory_item).exists()


def test_negative_initial_stock_is_refused_and_nothing_is_created(staff_client, product):
    before = product.variants.count()
    response = _add(staff_client, product, initial_stock=-3)
    assert response.status_code == 400
    assert product.variants.count() == before


def test_adjust_answers_with_the_new_totals(staff_client, product):
    variant = product.variants.get(pk=_add(staff_client, product, initial_stock=10).json()["data"]["id"])
    item_id = variant.inventory_item.pk
    response = staff_client.post(
        f"/api/v1/admin-api/inventory/{item_id}/adjust/", {"quantity_delta": 5, "reason": "توريد جديد"}, format="json"
    )
    assert response.status_code == 200
    assert response.json()["data"]["item"]["available_quantity"] == 15
