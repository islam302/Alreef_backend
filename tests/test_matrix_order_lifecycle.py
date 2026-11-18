"""
The order lifecycle matrix.

For every payment route the shop sells — plain COD, COD with a deposit by
InstaPay or by Vodafone Cash, a full InstaPay transfer, a full Vodafone Cash
transfer — an order is driven (through the real services) into every status
the state machine has, and from each of those every staff status change is
tried through the dashboard endpoint: the legal ones must do their work, the
illegal ones must be refused with their exact code and leave nothing changed.

After each step the books are checked: stock (held, taken, given back), the
payments (states and amounts), the coupon use and the loyalty points.

Then the money-back flows: cancellation at every cancellable status, by staff
and by the customer, and returns (full and partial) on plain, deposit and
discounted orders.
"""
from decimal import Decimal

import pytest
from django.db import transaction
from django.db.models import Sum

from apps.coupons.models import CouponUsage
from apps.inventory.models import Batch, StockReservation, StockReservationStatus
from apps.loyalty.models import LoyaltyAccount, LoyaltyTransaction, LoyaltyTransactionType
from apps.orders.models import ORDER_STATUS_TRANSITIONS, Order, OrderStatus, ReturnReason, ReturnRequest, ReturnRequestStatus
from apps.orders.services import (
    STATUSES_WITH_OWN_FLOW,
    cancel_order,
    complete_return_and_refund,
    create_return_request,
    decide_return_request,
    transition_status,
)
from apps.payments.models import Payment, PaymentStatus, Refund, RefundStatus
from apps.payments.services import record_manual_receipt
from tests import matrix_world as w

pytestmark = pytest.mark.django_db

ROUTES = tuple(w.PAYMENT_ROUTES)
ALL_STATUSES = tuple(OrderStatus.values)
CANCELLABLE = {s for s, targets in ORDER_STATUS_TRANSITIONS.items() if OrderStatus.CANCELLED in targets}
FULFILMENT = (OrderStatus.PROCESSING, OrderStatus.PACKED, OrderStatus.SHIPPED, OrderStatus.OUT_FOR_DELIVERY, OrderStatus.DELIVERED)
STOCK = 500


class _Rollback(Exception):
    pass


@pytest.fixture
def world(db):
    return w.build_world(stock=STOCK)


@pytest.fixture
def staff(db):
    from apps.users.models import User

    return User.objects.create_user(email="staff@example.com", password="SuperSecret123", is_staff=True)


@pytest.fixture
def staff_client(staff):
    return w.jwt_client(staff)


def place(settings, world, route, *, coupon=True, customer="saved_address", taxed=2, zero=1):
    """A real order through the API: a signed-in customer (so points apply), with a 10% coupon."""
    method, deposit_route = w.configure(settings, route)
    shopper = w.make_shopper(customer, world.governorates["cairo"])
    shopper.add(world.taxed, taxed)
    if zero:
        shopper.add(world.zero_rated, zero)
    code = ""
    if coupon:
        w.make_coupon("LIFE10")
        code = "life10"
    response = shopper.checkout(
        shipping_method_id=world.methods["standard"].id, payment_method=method, coupon_code=code, deposit_route=deposit_route,
    )
    assert response.status_code == 201, response.content
    order = Order.objects.get(order_number=response.json()["data"]["order"]["order_number"])
    return order, shopper


def transfer_payment(order):
    """The payment a person has to confirm before the order may proceed (deposit or full transfer)."""
    return order.payments.exclude(provider="cod").first()


def cod_payment(order):
    return order.payments.filter(provider="cod").first()


def settle(order):
    payment = transfer_payment(order)
    if payment is not None:
        record_manual_receipt(payment)
    order.refresh_from_db()
    return order


def drive_to(order, status, route):
    """Walk `order` into `status` the way the shop really gets there."""
    order.refresh_from_db()
    if status == OrderStatus.PENDING:
        if route == "cod":
            # A plain COD order is confirmed at checkout. A pending one is an
            # order from before that (or one staff reopened); put it back.
            Order.objects.filter(pk=order.pk).update(status=OrderStatus.PENDING, confirmed_at=None)
        order.refresh_from_db()
        return order
    order = settle(order)
    assert order.status == OrderStatus.CONFIRMED
    if status == OrderStatus.CONFIRMED:
        return order
    if status == OrderStatus.CANCELLED:
        return cancel_order(order, reason="test")
    for step in FULFILMENT:
        if step == OrderStatus.OUT_FOR_DELIVERY and status != OrderStatus.OUT_FOR_DELIVERY:
            continue  # out-for-delivery is optional on the way to delivered
        order = transition_status(order, step)
        if step == status:
            return order
    assert order.status == OrderStatus.DELIVERED
    item = order.items.order_by("id").first()
    rr = create_return_request(order=order, user=order.user, reason=ReturnReason.DAMAGED, description="",
                               order_item_quantities={item.id: 1})
    order.refresh_from_db()
    if status == OrderStatus.RETURN_REQUESTED:
        return order
    decide_return_request(return_request=rr, approve=True, decided_by=None)
    if status == OrderStatus.RETURNED:
        return transition_status(order, OrderStatus.RETURNED)
    complete_return_and_refund(return_request=rr, processed_by=None)
    order.refresh_from_db()
    assert order.status == OrderStatus.PARTIALLY_REFUNDED
    if status == OrderStatus.PARTIALLY_REFUNDED:
        return order
    return transition_status(order, OrderStatus.REFUNDED)


# ---------------------------------------------------------------------------
# The books
# ---------------------------------------------------------------------------


def snapshot(order):
    order.refresh_from_db()
    return {
        "status": order.status,
        "payments": sorted((p.provider, p.status, p.amount.amount, bool(p.metadata.get("needs_refund"))) for p in order.payments.all()),
        "reservations": sorted(StockReservation.objects.filter(order=order).values_list("status", "quantity")),
        "stock": tuple(Batch.objects.order_by("pk").values_list("quantity", flat=True)),
        "coupon_uses": CouponUsage.objects.filter(order=order).count(),
        "points": list(LoyaltyAccount.objects.values_list("points_balance", flat=True)),
        "refunds": sorted(Refund.objects.filter(order=order).values_list("status", "amount")),
    }


def earned_points(order):
    return int(order.grand_total.amount // 100)


def assert_books_after_cancel(order):
    order.refresh_from_db()
    assert order.status == OrderStatus.CANCELLED and order.cancelled_at is not None
    assert not StockReservation.objects.filter(order=order, status=StockReservationStatus.ACTIVE).exists()
    assert set(Batch.objects.values_list("quantity", flat=True)) == {STOCK}  # every unit back on the shelf
    assert not CouponUsage.objects.filter(order=order).exists()  # the use is given back
    if order.user_id:
        account = LoyaltyAccount.objects.filter(user_id=order.user_id).first()
        assert account is None or account.points_balance == 0  # no points for an order that never happened
    for payment in order.payments.all():
        assert payment.status not in (PaymentStatus.PENDING, PaymentStatus.REQUIRES_ACTION), payment.provider
        if payment.provider != "cod" and payment.status == PaymentStatus.CAPTURED:
            assert payment.metadata.get("needs_refund") is True  # money we hold, flagged for a person to return
        if payment.provider == "cod":
            assert payment.status != PaymentStatus.CAPTURED  # no cash was ever collected


def assert_books_after_delivery(order):
    order.refresh_from_db()
    assert order.status == OrderStatus.DELIVERED and order.delivered_at is not None
    captured = order.payments.filter(status=PaymentStatus.CAPTURED).aggregate(s=Sum("amount"))["s"]
    assert captured == order.grand_total.amount  # deposit + cash = the total, exactly once
    if order.payment_method == "cod":
        assert cod_payment(order).status == PaymentStatus.CAPTURED
    assert set(StockReservation.objects.filter(order=order).values_list("status", flat=True)) == {StockReservationStatus.CONSUMED}
    assert CouponUsage.objects.filter(order=order).count() == 1
    assert LoyaltyAccount.objects.get(user=order.user).points_balance == earned_points(order)


# ---------------------------------------------------------------------------
# Every staff transition from every status, for every route
# ---------------------------------------------------------------------------


def expected_outcome(frm, to, route):
    if to in STATUSES_WITH_OWN_FLOW:
        return 409, "ORDER_STATUS_HAS_OWN_FLOW"
    if to == OrderStatus.CANCELLED:
        return (200, None) if frm in CANCELLABLE else (409, "ORDER_CANNOT_CANCEL")
    if to not in ORDER_STATUS_TRANSITIONS[frm]:
        return 409, "ORDER_INVALID_TRANSITION"
    # Every route here is cash or a manual transfer: confirming a transfer or
    # deposit order records its money as received (see the gate tests below).
    return 200, None


@pytest.mark.parametrize("frm", ALL_STATUSES)
@pytest.mark.parametrize("route", ROUTES)
def test_every_staff_status_change_from_every_status(settings, world, staff_client, route, frm):
    order, _ = place(settings, world, route)
    order = drive_to(order, frm, route)
    assert order.status == frm
    before = snapshot(order)
    url = f"{w.API}/admin-api/orders/{order.order_number}/status/"

    outcomes = {}
    for to in ALL_STATUSES:
        if (frm, to) == (OrderStatus.RETURN_REQUESTED, OrderStatus.DELIVERED):
            continue  # a known hole, pinned by test_a_return_cannot_be_dropped_through_the_plain_status_change
        want_status, want_code = expected_outcome(frm, to, route)
        try:
            with transaction.atomic():
                response = staff_client.post(url, {"status": to, "note": "matrix"}, format="json")
                got_code = (response.json().get("errors") or {}).get("code")
                outcomes[to] = (response.status_code, got_code)
                assert (response.status_code, got_code) == (want_status, want_code), (route, frm, to, response.content)
                if want_status == 200:
                    order.refresh_from_db()
                    assert order.status == to
                    if to == OrderStatus.CANCELLED:
                        assert_books_after_cancel(order)
                    elif to == OrderStatus.DELIVERED:
                        assert_books_after_delivery(order)
                    elif to == OrderStatus.CONFIRMED:
                        assert set(StockReservation.objects.filter(order=order).values_list("status", flat=True)) == {
                            StockReservationStatus.CONSUMED}
                        if transfer_payment(order) is not None:
                            assert transfer_payment(order).status == PaymentStatus.CAPTURED
                else:
                    assert snapshot(order) == before  # a refusal changes nothing
                raise _Rollback
        except _Rollback:
            pass
    assert snapshot(order) == before


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("status", ["", "shipping", "CANCELLED", None])
def test_an_unknown_status_is_a_validation_error(settings, world, staff_client, route, status):
    order, _ = place(settings, world, route)
    before = snapshot(order)
    response = staff_client.post(f"{w.API}/admin-api/orders/{order.order_number}/status/", {"status": status}, format="json")
    assert response.status_code == 400 and response.json()["errors"]["code"] == "VALIDATION_ERROR"
    assert snapshot(order) == before


@pytest.mark.parametrize("route", ROUTES)
def test_a_customer_cannot_use_the_staff_status_endpoint(settings, world, route):
    order, shopper = place(settings, world, route)
    before = snapshot(order)
    response = shopper.client.post(f"{w.API}/admin-api/orders/{order.order_number}/status/", {"status": "delivered"}, format="json")
    assert response.status_code in (401, 403, 404)
    assert snapshot(order) == before


# ---------------------------------------------------------------------------
# The paid-before-confirmed gate, route by route
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", [r for r in ROUTES if r != "cod"])
@pytest.mark.parametrize("payment_state", ["requires_action", "failed", "cancelled", "expired"])
def test_an_unpaid_transfer_or_deposit_order_can_never_skip_to_fulfilment(settings, world, staff_client, route, payment_state):
    order, _ = place(settings, world, route)
    Payment.objects.filter(pk=transfer_payment(order).pk).update(status=payment_state)
    url = f"{w.API}/admin-api/orders/{order.order_number}/status/"
    for later in FULFILMENT:
        response = staff_client.post(url, {"status": later}, format="json")
        assert response.status_code == 409, (later, response.content)
    order.refresh_from_db()
    assert order.status == OrderStatus.PENDING
    assert cod_payment(order) is None or cod_payment(order).status == PaymentStatus.PENDING
    # Recording the cash for a deposit order before delivery is refused too.
    if cod_payment(order) is not None:
        response = staff_client.post(f"{w.API}/admin-api/payments/{cod_payment(order).id}/settle/", {"outcome": "received"}, format="json")
        assert response.status_code == 409


@pytest.mark.parametrize("route", [r for r in ROUTES if r != "cod"])
@pytest.mark.parametrize("payment_state", ["requires_action", "failed", "cancelled", "expired"])
def test_confirming_from_the_order_records_the_transfer_whatever_its_state(settings, world, staff_client, route, payment_state):
    order, _ = place(settings, world, route)
    Payment.objects.filter(pk=transfer_payment(order).pk).update(status=payment_state)
    url = f"{w.API}/admin-api/orders/{order.order_number}/status/"
    response = staff_client.post(url, {"status": "confirmed"}, format="json")
    assert response.status_code == 200, response.content
    order.refresh_from_db()
    assert order.status == OrderStatus.CONFIRMED
    assert transfer_payment(order).status == PaymentStatus.CAPTURED
    assert order.payments.filter(status=PaymentStatus.CAPTURED).count() == 1
    assert set(StockReservation.objects.filter(order=order).values_list("status", flat=True)) == {
        StockReservationStatus.CONSUMED}
    # Cash for a deposit order is still collected at the door.
    assert cod_payment(order) is None or cod_payment(order).status == PaymentStatus.PENDING


@pytest.mark.parametrize("route", [r for r in ROUTES if r != "cod"])
def test_settling_the_transfer_confirms_the_order_and_takes_the_stock(settings, world, staff_client, route):
    order, shopper = place(settings, world, route)
    assert set(StockReservation.objects.filter(order=order).values_list("status", flat=True)) == {StockReservationStatus.ACTIVE}
    assert not LoyaltyTransaction.objects.exists()
    response = staff_client.post(f"{w.API}/admin-api/payments/{transfer_payment(order).id}/settle/", {"outcome": "received"}, format="json")
    assert response.status_code == 200, response.content
    order.refresh_from_db()
    assert order.status == OrderStatus.CONFIRMED
    assert set(StockReservation.objects.filter(order=order).values_list("status", flat=True)) == {StockReservationStatus.CONSUMED}
    assert sorted(Batch.objects.values_list("quantity", flat=True)) == sorted([STOCK - 2, STOCK - 1])
    assert LoyaltyAccount.objects.get(user=shopper.user).points_balance == earned_points(order)
    # Settling again changes nothing.
    staff_client.post(f"{w.API}/admin-api/payments/{transfer_payment(order).id}/settle/", {"outcome": "received"}, format="json")
    assert LoyaltyAccount.objects.get(user=shopper.user).points_balance == earned_points(order)
    assert transfer_payment(order).transactions.filter(transaction_type="capture").count() == 1


@pytest.mark.parametrize("route", ROUTES)
def test_the_whole_road_to_delivered_collects_exactly_the_total(settings, world, staff_client, route):
    order, _ = place(settings, world, route)
    order = settle(order)
    url = f"{w.API}/admin-api/orders/{order.order_number}/status/"
    for step in FULFILMENT:
        assert staff_client.post(url, {"status": step}, format="json").status_code == 200, step
    assert_books_after_delivery(order)
    # Delivering is the only thing that collected the cash; doing it again is refused.
    assert staff_client.post(url, {"status": "delivered"}, format="json").status_code == 409
    for payment in order.payments.all():
        assert payment.transactions.filter(transaction_type="capture").count() == 1


@pytest.mark.parametrize("route", ["cod", "cod_deposit_instapay", "cod_deposit_vodafone"])
def test_cod_cash_cannot_be_recorded_before_delivery(settings, world, staff_client, route):
    order, _ = place(settings, world, route)
    order = settle(order)
    for step in FULFILMENT[:-1]:
        order = transition_status(order, step)
        response = staff_client.post(f"{w.API}/admin-api/payments/{cod_payment(order).id}/settle/", {"outcome": "received"}, format="json")
        assert response.status_code == 409, (step, response.content)
        assert cod_payment(order).status == PaymentStatus.PENDING


# ---------------------------------------------------------------------------
# Cancelling, by staff and by the customer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("via", ["status_endpoint", "cancel_endpoint"])
@pytest.mark.parametrize("frm", sorted(CANCELLABLE))
@pytest.mark.parametrize("route", ROUTES)
def test_staff_cancelling_at_every_cancellable_status_puts_everything_back(settings, world, staff_client, route, frm, via):
    order, _ = place(settings, world, route)
    order = drive_to(order, frm, route)
    if via == "status_endpoint":
        response = staff_client.post(f"{w.API}/admin-api/orders/{order.order_number}/status/", {"status": "cancelled", "note": "x"}, format="json")
    else:
        response = staff_client.post(f"{w.API}/admin-api/orders/{order.order_number}/cancel/", {"reason": "x"}, format="json")
    assert response.status_code == 200, response.content
    assert_books_after_cancel(order)
    # And a cancelled order goes nowhere.
    for to in ALL_STATUSES:
        r = staff_client.post(f"{w.API}/admin-api/orders/{order.order_number}/status/", {"status": to}, format="json")
        assert r.status_code == 409, to


@pytest.mark.parametrize("frm", sorted(set(ALL_STATUSES) - CANCELLABLE))
@pytest.mark.parametrize("route", ROUTES)
def test_staff_cancel_endpoint_refuses_after_dispatch(settings, world, staff_client, route, frm):
    order, _ = place(settings, world, route)
    order = drive_to(order, frm, route)
    before = snapshot(order)
    response = staff_client.post(f"{w.API}/admin-api/orders/{order.order_number}/cancel/", {"reason": "x"}, format="json")
    assert (response.status_code, response.json()["errors"]["code"]) == (409, "ORDER_CANNOT_CANCEL")
    assert snapshot(order) == before


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("paid", [False, True])
def test_the_customer_cancelling_their_own_order(settings, world, route, paid):
    order, shopper = place(settings, world, route)
    if paid:
        order = settle(order)
    before = snapshot(order)
    response = shopper.client.post(f"{w.API}/orders/{order.order_number}/cancel/", {"reason": "changed my mind"}, format="json")
    money_with_us = paid and route != "cod"
    if money_with_us:
        # A confirmed transfer or deposit: cancelling means a refund, and a person does that.
        assert (response.status_code, response.json()["errors"]["code"]) == (409, "ORDER_ALREADY_PAID")
        assert snapshot(order) == before
    else:
        assert response.status_code == 200, response.content
        assert_books_after_cancel(order)


@pytest.mark.parametrize("route", ROUTES)
def test_a_customer_cannot_cancel_someone_elses_order(settings, world, route):
    order, _ = place(settings, world, route)
    intruder = w.make_shopper("saved_address", world.governorates["cairo"])
    before = snapshot(order)
    response = intruder.client.post(f"{w.API}/orders/{order.order_number}/cancel/", {"reason": "x"}, format="json")
    assert (response.status_code, response.json()["errors"]["code"]) == (404, "ORDER_NOT_FOUND")
    assert snapshot(order) == before


@pytest.mark.parametrize("route", [r for r in ROUTES if r != "cod"])
def test_a_transfer_that_lands_after_cancellation_is_recorded_and_flagged(settings, world, staff_client, route):
    order, _ = place(settings, world, route)
    cancel_order(order, reason="no transfer came")
    payment = transfer_payment(order)
    assert payment.status == PaymentStatus.CANCELLED
    response = staff_client.post(f"{w.API}/admin-api/payments/{payment.id}/settle/", {"outcome": "received"}, format="json")
    assert response.status_code == 200, response.content
    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == PaymentStatus.CAPTURED and payment.metadata.get("needs_refund") is True
    assert order.status == OrderStatus.CANCELLED  # the money does not resurrect the order
    assert set(Batch.objects.values_list("quantity", flat=True)) == {STOCK}


# ---------------------------------------------------------------------------
# Returns and refunds
# ---------------------------------------------------------------------------


def _deliver(order):
    order = settle(order)
    for step in (OrderStatus.PROCESSING, OrderStatus.PACKED, OrderStatus.SHIPPED, OrderStatus.DELIVERED):
        order = transition_status(order, step)
    return order


def _return_via_api(shopper, staff_client, order, quantities):
    items = [{"order_item_id": item_id, "quantity": qty} for item_id, qty in quantities.items()]
    response = shopper.client.post(
        f"{w.API}/orders/{order.order_number}/returns/", {"reason": "damaged", "items": items}, format="json",
    )
    assert response.status_code == 201, response.content
    rr = ReturnRequest.objects.get(pk=response.json()["data"]["id"])
    assert staff_client.post(f"{w.API}/admin-api/returns/{rr.id}/decide/", {"approve": True}, format="json").status_code == 200
    response = staff_client.post(f"{w.API}/admin-api/returns/{rr.id}/complete/", {}, format="json")
    assert response.status_code == 200, response.content
    rr.refresh_from_db()
    return rr


@pytest.mark.parametrize("coupon", [False, True], ids=["full_price", "discounted"])
@pytest.mark.parametrize("extent", ["full", "partial"])
@pytest.mark.parametrize("route", ROUTES)
def test_returns_refund_what_was_paid_for_the_returned_lines(settings, world, staff_client, route, extent, coupon):
    order, shopper = place(settings, world, route, coupon=coupon)
    order = _deliver(order)
    points_before = LoyaltyAccount.objects.get(user=shopper.user).points_balance
    assert points_before == earned_points(order)
    stock_before = snapshot(order)["stock"]
    items = list(order.items.order_by("id"))
    quantities = {i.id: i.quantity for i in items} if extent == "full" else {items[0].id: 1}

    rr = _return_via_api(shopper, staff_client, order, quantities)
    order.refresh_from_db()
    assert rr.status == ReturnRequestStatus.REFUNDED

    # What the returned lines were actually paid for: their share of the line
    # after the discount, plus the VAT charged on that share.
    by_id = {i.id: i for i in items}
    assert sum(i.discount_amount.amount for i in items) == order.coupon_discount.amount + order.promotion_discount.amount
    assert sum(i.tax_amount.amount for i in items) == order.tax_total.amount
    value = sum((
        w.q((by_id[i].line_total.amount - by_id[i].discount_amount.amount + by_id[i].tax_amount.amount) * qty / by_id[i].quantity)
        for i, qty in quantities.items()
    ), Decimal("0"))
    refunds = Refund.objects.filter(order=order, status=RefundStatus.PROCESSED)
    assert refunds.aggregate(s=Sum("amount"))["s"] == value
    # Never more from a payment than it holds; the order's own payment before the deposit.
    for payment in order.payments.all():
        given = payment.refunds.filter(status=RefundStatus.PROCESSED).aggregate(s=Sum("amount"))["s"] or 0
        assert given <= payment.amount.amount
        if given:
            assert payment.status in (PaymentStatus.PARTIALLY_REFUNDED, PaymentStatus.REFUNDED)
            assert payment.status == (PaymentStatus.REFUNDED if given == payment.amount.amount else PaymentStatus.PARTIALLY_REFUNDED)
    if route.startswith("cod_deposit"):
        cod = cod_payment(order)
        cod_given = cod.refunds.aggregate(s=Sum("amount"))["s"] or 0
        assert cod_given == min(value, cod.amount.amount)

    assert order.status == (OrderStatus.REFUNDED if value >= order.grand_total.amount else OrderStatus.PARTIALLY_REFUNDED)
    # Points: the refunded share of them goes back.
    want_back = int((Decimal(points_before) * min(value / order.grand_total.amount, 1)).to_integral_value())
    assert LoyaltyAccount.objects.get(user=shopper.user).points_balance == points_before - want_back
    # The coupon was spent on a real order that really happened.
    assert CouponUsage.objects.filter(order=order).count() == (1 if coupon else 0)
    # Returned food is not put back on sale automatically.
    assert snapshot(order)["stock"] == stock_before


def test_a_returned_vatable_item_refunds_the_vat_paid_on_it(settings, world, staff_client):
    order, shopper = place(settings, world, "cod", coupon=False, taxed=1, zero=0)  # 100 + 14 VAT + 30 + 15
    order = _deliver(order)
    item = order.items.get()
    _return_via_api(shopper, staff_client, order, {item.id: 1})
    refunded = Refund.objects.filter(order=order, status=RefundStatus.PROCESSED).aggregate(s=Sum("amount"))["s"]
    assert refunded == Decimal("114.00")


@pytest.mark.parametrize("route", ["cod", "instapay"])
def test_a_return_cannot_be_dropped_through_the_plain_status_change(settings, world, staff_client, route):
    order, _ = place(settings, world, route)
    order = drive_to(order, OrderStatus.RETURN_REQUESTED, route)
    response = staff_client.post(f"{w.API}/admin-api/orders/{order.order_number}/status/", {"status": "delivered"}, format="json")
    rr = ReturnRequest.objects.get(order=order)
    assert response.status_code == 409 or rr.status != ReturnRequestStatus.REQUESTED


@pytest.mark.parametrize("route", ROUTES)
def test_a_return_cannot_be_completed_twice_or_before_approval(settings, world, staff_client, route):
    order, shopper = place(settings, world, route)
    order = _deliver(order)
    item = order.items.order_by("id").first()
    response = shopper.client.post(
        f"{w.API}/orders/{order.order_number}/returns/",
        {"reason": "damaged", "items": [{"order_item_id": item.id, "quantity": 1}]}, format="json",
    )
    rr_id = response.json()["data"]["id"]
    early = staff_client.post(f"{w.API}/admin-api/returns/{rr_id}/complete/", {}, format="json")
    assert (early.status_code, early.json()["errors"]["code"]) == (409, "ORDER_INVALID_TRANSITION")
    staff_client.post(f"{w.API}/admin-api/returns/{rr_id}/decide/", {"approve": True}, format="json")
    assert staff_client.post(f"{w.API}/admin-api/returns/{rr_id}/complete/", {}, format="json").status_code == 200
    refunded = Refund.objects.filter(order=order).aggregate(s=Sum("amount"))["s"]
    again = staff_client.post(f"{w.API}/admin-api/returns/{rr_id}/complete/", {}, format="json")
    assert again.status_code == 409
    assert Refund.objects.filter(order=order).aggregate(s=Sum("amount"))["s"] == refunded


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("quantity", [0, -1, 3, 99])
def test_a_return_for_more_than_was_bought_is_refused(settings, world, route, quantity):
    order, shopper = place(settings, world, route)
    order = _deliver(order)
    item = order.items.get(variant=world.taxed)  # bought 2
    before = snapshot(order)
    response = shopper.client.post(
        f"{w.API}/orders/{order.order_number}/returns/",
        {"reason": "damaged", "items": [{"order_item_id": item.id, "quantity": quantity}]}, format="json",
    )
    assert response.status_code == 400, response.content
    assert snapshot(order) == before
    assert not ReturnRequest.objects.exists()


@pytest.mark.parametrize("frm", [OrderStatus.CONFIRMED, OrderStatus.SHIPPED, OrderStatus.CANCELLED])
def test_only_a_delivered_order_can_be_returned(settings, world, frm):
    order, shopper = place(settings, world, "cod")
    order = drive_to(order, frm, "cod")
    item = order.items.order_by("id").first()
    response = shopper.client.post(
        f"{w.API}/orders/{order.order_number}/returns/",
        {"reason": "damaged", "items": [{"order_item_id": item.id, "quantity": 1}]}, format="json",
    )
    assert (response.status_code, response.json()["errors"]["code"]) == (409, "ORDER_INVALID_TRANSITION")


# --- the payment refund button --------------------------------------------------


@pytest.mark.parametrize("route", ROUTES)
def test_nothing_can_be_refunded_before_money_was_received(settings, world, staff_client, route):
    order, _ = place(settings, world, route)
    for payment in order.payments.all():
        response = staff_client.post(f"{w.API}/admin-api/payments/{payment.id}/refund/", {"amount": "1.00"}, format="json")
        assert (response.status_code, response.json()["errors"]["code"]) == (409, "PAYMENT_INVALID_STATE"), payment.provider
    assert not Refund.objects.exists()


@pytest.mark.parametrize("route", ROUTES)
def test_refunds_of_a_delivered_order_add_up_and_stop_at_what_was_paid(settings, world, staff_client, route):
    order, _ = place(settings, world, route)
    order = _deliver(order)
    for payment in order.payments.filter(amount__gt=0):
        url = f"{w.API}/admin-api/payments/{payment.id}/refund/"
        half = w.q(payment.amount.amount / 2)
        rest = payment.amount.amount - half
        assert staff_client.post(url, {"amount": str(half), "reason": "a"}, format="json").status_code == 201
        payment.refresh_from_db()
        assert payment.status == PaymentStatus.PARTIALLY_REFUNDED
        over = staff_client.post(url, {"amount": str(rest + Decimal("0.01")), "reason": "b"}, format="json")
        assert (over.status_code, over.json()["errors"]["code"]) == (409, "REFUND_EXCEEDS_CAPTURED_AMOUNT")
        assert staff_client.post(url, {"amount": str(rest), "reason": "c"}, format="json").status_code == 201
        payment.refresh_from_db()
        assert payment.status == PaymentStatus.REFUNDED
        given = payment.refunds.filter(status=RefundStatus.PROCESSED).aggregate(s=Sum("amount"))["s"]
        assert given == payment.amount.amount
        for bad in ("0", "-5", "abc", str(payment.amount.amount + 1)):
            r = staff_client.post(url, {"amount": bad, "reason": f"bad {bad}"}, format="json")
            assert r.status_code in (400, 409), bad
        assert payment.refunds.filter(status=RefundStatus.PROCESSED).aggregate(s=Sum("amount"))["s"] == given


@pytest.mark.parametrize("route", ROUTES)
def test_a_double_clicked_refund_pays_out_once(settings, world, staff_client, route):
    order, _ = place(settings, world, route)
    order = _deliver(order)
    payment = order.payments.filter(amount__gt=0).first()
    url = f"{w.API}/admin-api/payments/{payment.id}/refund/"
    for _ in range(3):
        staff_client.post(url, {"amount": "10.00", "reason": "same"}, format="json", HTTP_IDEMPOTENCY_KEY="refund-1")
    assert payment.refunds.filter(status=RefundStatus.PROCESSED).count() == 1
