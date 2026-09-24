"""
Two checkouts arriving at the same moment.

Both bugs these cover were live, and they were entangled: the order-number
collision crashed the second request before anything could notice it was
converting a cart that was already spent. Fixing either one alone would have
made the other worse - remove the collision and the duplicate order goes
through silently; lock the cart and legitimate concurrent customers still get a
500. So they are tested together, in the order they actually happen.

These need real threads and committed rows, hence `transaction=True`.
"""
import threading
import uuid

import pytest
from django.db import connection

from apps.carts.models import CartStatus
from apps.inventory.models import InventoryItem
from apps.orders.models import Order, OrderItem
from apps.users.models import Address

API = "/api/v1"


def run_parallel(fn, n=2, timeout=40):
    """Run fn(i) in n threads released together, so they genuinely overlap."""
    results, errors = [], []
    barrier = threading.Barrier(n)
    lock = threading.Lock()

    def worker(i):
        try:
            barrier.wait(timeout=15)
            out = fn(i)
            with lock:
                results.append(out)
        except Exception as exc:  # noqa: BLE001 - recording, not handling
            with lock:
                errors.append(repr(exc)[:300])
        finally:
            # Each thread gets its own connection; leaving it open leaks it into
            # the next test and makes the whole module flaky.
            connection.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout)
    return results, errors


def _client_for(user):
    from rest_framework.test import APIClient
    from rest_framework_simplejwt.tokens import AccessToken

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    return client


def _add_to_cart(user, variant, quantity=1):
    return _client_for(user).post(
        f"{API}/cart/items/", {"variant_id": variant.id, "quantity": quantity}, format="json"
    )


def _checkout(user, address, shipping_method_id, idempotency_key=None):
    """A fresh key per call by default - which is what a client that mints a
    UUID per click does, and what the old code could not survive."""
    return _client_for(user).post(
        f"{API}/checkout/",
        {
            "address_id": address.id,
            "shipping_method_id": shipping_method_id,
            "payment_method": "cod",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=idempotency_key or str(uuid.uuid4()),
    )


def _bare_order_fields(user):
    """The minimum a valid Order row needs - the money columns are NOT NULL."""
    from djmoney.money import Money

    zero = Money(0, "EGP")
    return {
        "user": user, "currency": "EGP", "customer_email": user.email,
        "subtotal": zero, "coupon_discount": zero, "promotion_discount": zero,
        "tax_total": zero, "shipping_total": zero, "cod_fee": zero, "grand_total": zero,
    }


def _stock(item):
    return InventoryItem.objects.with_stock().get(pk=item.pk)


@pytest.fixture
def other_address(other_user, governorate):
    return Address.objects.create(
        user=other_user, label="home", full_name="Other Person", phone="+201000000999",
        governorate=governorate, street="1 Street", building="1",
    )


# --------------------------------------------------------------------- F-01
@pytest.mark.django_db(transaction=True)
def test_two_customers_checking_out_together_both_succeed(
    user, other_user, address, other_address, shipping_setup, product_with_variant_and_stock
):
    """
    Nothing hostile here - two unrelated people buying honey in the same second.

    `generate_order_number` read the highest number for today and added one, so
    both requests produced ORD-<today>-00001 and the second INSERT violated the
    unique constraint. One customer lost their order to a 500.
    """
    batch = product_with_variant_and_stock["batch"]
    batch.quantity = 100
    batch.save(update_fields=["quantity"])
    variant = product_with_variant_and_stock["variant"]
    for u in (user, other_user):
        _add_to_cart(u, variant)

    pairs = [(user, address), (other_user, other_address)]
    results, errors = run_parallel(
        lambda i: _checkout(*pairs[i], shipping_setup["method"].id).status_code, n=2
    )

    assert not errors, f"a checkout thread raised: {errors}"
    assert results == [201, 201], f"expected both to succeed, got {sorted(results)}"
    numbers = set(Order.objects.values_list("order_number", flat=True))
    assert len(numbers) == 2, f"two orders should carry two distinct numbers, got {numbers}"


@pytest.mark.django_db(transaction=True)
def test_ten_simultaneous_customers_all_get_distinct_order_numbers(
    user, address, shipping_setup, product_with_variant_and_stock, governorate
):
    """
    Two threads leave a window for luck. Ten does not: with the old code this
    fails essentially every run, and it also exercises the retry more than once.

    Ten separate customers, because one customer's cart can only convert once
    (that is the next test).
    """
    from apps.users.models import User

    batch = product_with_variant_and_stock["batch"]
    batch.quantity = 500
    batch.save(update_fields=["quantity"])
    variant = product_with_variant_and_stock["variant"]

    shoppers = []
    for i in range(10):
        u = User.objects.create_user(email=f"rush{i}@example.com", password="Str0ng-Passw0rd-x")
        a = Address.objects.create(
            user=u, label="home", full_name=f"Shopper {i}", phone=f"+2010000010{i:02d}",
            governorate=governorate, street="1 Street", building="1",
        )
        _add_to_cart(u, variant)
        shoppers.append((u, a))

    results, errors = run_parallel(
        lambda i: _checkout(*shoppers[i], shipping_setup["method"].id).status_code, n=10
    )

    assert not errors, f"threads raised: {errors}"
    assert results == [201] * 10, f"not every checkout succeeded: {sorted(results)}"
    numbers = list(Order.objects.values_list("order_number", flat=True))
    assert len(set(numbers)) == 10, f"duplicate order numbers issued: {sorted(numbers)}"


# --------------------------------------------------------------------- F-02
@pytest.mark.django_db(transaction=True)
def test_one_cart_cannot_become_two_orders(
    user, address, shipping_setup, product_with_variant_and_stock
):
    """
    Two tabs, or a double click on a client that mints a fresh Idempotency-Key
    each time. Both requests carry a *different* key, so neither the mixin's
    cache lock nor Payment's unique constraint sees a duplicate - and before the
    cart was locked, both converted it: two orders, two stock deductions and two
    COD liabilities for one basket.
    """
    batch = product_with_variant_and_stock["batch"]
    batch.quantity = 100
    batch.save(update_fields=["quantity"])
    item = product_with_variant_and_stock["item"]
    variant = product_with_variant_and_stock["variant"]
    _add_to_cart(user, variant, quantity=1)

    before = _stock(item).on_hand_total

    results, errors = run_parallel(
        lambda i: _checkout(user, address, shipping_setup["method"].id).status_code, n=2
    )

    assert not errors, f"threads raised: {errors}"
    assert Order.objects.count() == 1, (
        f"one cart produced {Order.objects.count()} orders; responses were {sorted(results)}"
    )
    assert sorted(results) == [201, 409], f"expected one 201 and one 409, got {sorted(results)}"

    # The point of the bug was the stock, not the row count.
    assert OrderItem.objects.count() == 1
    assert _stock(item).on_hand_total == before - 1, (
        f"a one-unit cart moved {before - _stock(item).on_hand_total} units of stock"
    )


@pytest.mark.django_db
def test_a_converted_cart_is_refused_deterministically(
    authenticated_client, user, address, shipping_setup, product_with_variant_and_stock
):
    """
    The same rule without threads, so a regression shows up as a clear 409
    rather than as a flaky concurrency test.
    """
    from apps.carts.models import Cart

    variant = product_with_variant_and_stock["variant"]
    _add_to_cart(user, variant)
    first = _checkout(user, address, shipping_setup["method"].id)
    assert first.status_code == 201, first.content

    # Re-open the (now converted) cart's items and try again against it.
    cart = Cart.objects.filter(user=user).order_by("-created_at").first()
    assert cart.status == CartStatus.CONVERTED

    from apps.checkout.services import create_order_from_cart
    from apps.core.error_codes import ApplicationError, ErrorCodes

    with pytest.raises(ApplicationError) as exc:
        create_order_from_cart(
            user=user, cart=cart, address=address,
            shipping_method_id=shipping_setup["method"].id,
            payment_method="cod", idempotency_key=str(uuid.uuid4()),
        )
    assert exc.value.code == ErrorCodes.CART_ALREADY_CHECKED_OUT


# ------------------------------------------------- the allocator in isolation
@pytest.mark.django_db
def test_order_numbers_from_the_sequence_never_repeat(db):
    """The sequence is the guarantee; this is the guarantee stated as a test."""
    from apps.orders.services import generate_order_number

    numbers = [generate_order_number() for _ in range(50)]
    assert len(set(numbers)) == 50
    # Monotonic, so staff can tell which order came first.
    assert numbers == sorted(numbers)


@pytest.mark.django_db
def test_allocation_recovers_from_a_number_inserted_outside_the_sequence(user):
    """
    The one case `nextval` cannot cover: a row whose number never came from the
    sequence. Rewinding the sequence reproduces exactly that - the next
    allocation collides - and the retry has to carry it.
    """
    from django.db import connection

    from apps.orders.services import (
        ORDER_NUMBER_SEQUENCE,
        create_order_with_unique_number,
        generate_order_number,
    )

    taken = generate_order_number()
    Order.objects.create(order_number=taken, **_bare_order_fields(user))

    # Put the sequence back so the next call hands out the number just used.
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT setval('{ORDER_NUMBER_SEQUENCE}', %s)",
            [int(taken.rsplit("-", 1)[-1]) - 1],
        )

    order = create_order_with_unique_number(**_bare_order_fields(user))
    assert order.order_number != taken
    assert Order.objects.filter(order_number=order.order_number).count() == 1


@pytest.mark.django_db
def test_allocation_does_not_swallow_an_unrelated_constraint(user, monkeypatch):
    """
    The retry must stay specific. A different IntegrityError has to surface
    immediately, not be looped over and then re-raised as if it were a clash.
    """
    from django.db import IntegrityError

    from apps.orders import services as order_services

    calls = {"n": 0}

    def boom(*args, **kwargs):
        calls["n"] += 1
        raise IntegrityError('duplicate key value violates unique constraint "some_other_key"')

    monkeypatch.setattr(order_services.Order.objects, "create", boom)
    with pytest.raises(IntegrityError):
        order_services.create_order_with_unique_number(**_bare_order_fields(user))
    assert calls["n"] == 1, f"an unrelated constraint was retried {calls['n']} times"
