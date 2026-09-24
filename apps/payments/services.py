"""
Everything that moves money, or moves our record of money.

The rules this module exists to enforce, none of which an adapter is trusted
with:

* The amount is ours. It is computed from the cart, written onto the Payment at
  creation, and every later statement about it — a webhook's `amount`, a
  reconciliation's total — is checked against that value rather than replacing
  it.
* The currency is ours, and it is on an allowlist.
* A status only changes through `state.assert_transition`.
* An order is confirmed as paid by a verified webhook or a server-to-server
  reconciliation. Never by a browser arriving back from a gateway.
* Duplicate work is a no-op, not a second charge: the webhook table is unique on
  the provider's event id, refunds are unique on their idempotency key, and
  payments are unique on the checkout key.
* Rows that decide outcomes are locked before they are read.

`docs/payments/FLOWS.md` walks the happy path and the four failure paths.
"""
import logging
import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from djmoney.money import Money

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.payments import config as payment_config
from apps.payments import state as payment_state
from apps.payments.models import (
    Payment,
    PaymentAttempt,
    PaymentStatus,
    PaymentTransaction,
    PaymentTransactionType,
    PaymentWebhookEvent,
    Refund,
    RefundStatus,
)
from apps.payments.providers import get_payment_provider
from apps.payments.providers.http import GatewayError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def assert_currency_allowed(currency) -> None:
    """
    Reject a currency the payment layer is not configured for.

    Checked before a provider is contacted. Gateway integration IDs are
    currency-specific, so sending EGP amounts to a USD integration produces a
    successful charge for the wrong sum — which is the kind of bug that is only
    found in a reconciliation weeks later.
    """
    code = str(currency).upper()
    allowed = getattr(settings, "PAYMENT_ALLOWED_CURRENCIES", ["EGP"])
    if code not in allowed:
        raise ApplicationError(
            ErrorCodes.PAYMENT_CURRENCY_MISMATCH,
            f"Currency '{code}' is not accepted. Allowed: {', '.join(allowed)}.",
        )


def _new_merchant_reference(order) -> str:
    """
    The reference we give the provider and demand back on the callback.

    Includes the order number so a human reading a provider dashboard can find
    the order, plus entropy so a retried payment on the same order is a
    distinguishable transaction rather than a collision. Capped at 64 characters
    because `Payment.merchant_reference` is, and because a customer has to be
    able to type it into a transfer note.
    """
    return f"{order.order_number}-{uuid.uuid4().hex[:8].upper()}"[:64]


def _set_status(payment: Payment, new_status: str, *, failure_reason: str = "") -> bool:
    """
    Move a payment's status, or raise if that is not allowed.

    Returns True when something changed. Callers use that to decide whether to
    fire side effects, so re-delivering a webhook confirms nothing twice.
    """
    payment_state.assert_transition(payment.status, new_status)
    if payment.status == new_status and not failure_reason:
        return False

    fields = ["status"]
    payment.status = new_status
    if failure_reason:
        payment.failure_reason = failure_reason[:500]
        fields.append("failure_reason")
    payment.save(update_fields=fields)
    return True


def _redact(raw):
    from apps.core.logging_filters import redact

    if raw is None:
        return {}
    if isinstance(raw, dict):
        return redact(raw)
    if isinstance(raw, list):
        return {"items": redact(raw)}
    return {"raw": str(raw)[:2000]}


# ---------------------------------------------------------------------------
# Creating a payment
# ---------------------------------------------------------------------------


@transaction.atomic
def create_payment_for_order(
    *, order, provider_code: str, method: str, idempotency_key: str = ""
) -> Payment:
    """
    Create the Payment row, then ask the gateway to start the payment.

    The row is written before the gateway is called, deliberately. If the call
    times out we do not know whether the gateway created a transaction, and a
    row with a `merchant_reference` is what lets reconciliation ask. Calling
    first and recording afterwards loses exactly the payments that need finding.
    """
    assert_currency_allowed(order.grand_total.currency)

    if idempotency_key:
        # The cache-backed mixin normally catches a retry before it reaches
        # here. This is the path when the cache was flushed between attempts.
        existing = Payment.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing

    payment = Payment(
        order=order,
        provider=provider_code,
        method=method,
        amount=order.grand_total,
        status=PaymentStatus.PENDING,
        idempotency_key=idempotency_key or "",
    )
    payment.merchant_reference = _new_merchant_reference(order)
    try:
        payment.save()
    except IntegrityError:
        # Lost a race on the unique idempotency key: the winner's row is the
        # one that counts.
        existing = Payment.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing
        raise

    try:
        provider = get_payment_provider(provider_code)
        result = provider.create_payment(payment=payment, order=order)
    except GatewayError as exc:
        logger.warning("payment %s: gateway unreachable: %s", payment.id, exc.detail)
        _set_status(payment, PaymentStatus.FAILED, failure_reason=exc.message)
        _record_attempt(payment, PaymentStatus.FAILED, exc.message)
        raise ApplicationError(ErrorCodes.PAYMENT_PROVIDER_ERROR, exc.message) from exc

    if not result.success:
        message = result.error or "Payment could not be initiated."
        _set_status(payment, PaymentStatus.FAILED, failure_reason=message)
        _record_attempt(payment, PaymentStatus.FAILED, message)
        PaymentTransaction.objects.create(
            payment=payment,
            transaction_type=PaymentTransactionType.AUTHORIZE,
            amount=payment.amount,
            success=False,
            raw_response=_redact(result.raw),
        )
        raise ApplicationError(ErrorCodes.PAYMENT_FAILED, message)

    # The adapter reports where the customer now stands: pending (nothing to do
    # but wait), requires_action (a redirect or a reference to go and pay with),
    # or authorized. Validated rather than trusted, and NOT run through
    # PROVIDER_STATUS_MAP — that table translates a gateway's webhook vocabulary,
    # which is a different language from this one.
    initial_status = payment_state.assert_initial_status(provider_code, result.status)
    payment.provider_reference = result.provider_reference or ""
    payment.metadata = {
        **payment.metadata,
        **(result.metadata or {}),
        # Only ever non-secret navigation data.
        "redirect_url": result.redirect_url,
        "iframe_url": result.iframe_url,
    }
    payment.save(update_fields=["provider_reference", "metadata"])
    _set_status(payment, initial_status)

    _record_attempt(payment, payment.status, "")
    PaymentTransaction.objects.create(
        payment=payment,
        transaction_type=PaymentTransactionType.AUTHORIZE,
        amount=payment.amount,
        provider_transaction_id=result.provider_reference,
        success=True,
        raw_response=_redact(result.raw),
    )
    return payment


def _record_attempt(payment: Payment, status: str, error_message: str) -> None:
    number = payment.attempts.count() + 1
    PaymentAttempt.objects.create(
        payment=payment,
        attempt_number=number,
        status=status,
        error_message=(error_message or "")[:500],
    )


# ---------------------------------------------------------------------------
# Settling
# ---------------------------------------------------------------------------


@transaction.atomic
def mark_payment_captured(payment: Payment, *, note: str = ""):
    """
    The money is ours. Confirm the order.

    Only reachable from a verified webhook, a server-side reconciliation, or a
    staff member recording a cash collection. Nothing a browser sends reaches
    this function.
    """
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.status == PaymentStatus.CAPTURED:
        return payment  # Idempotent: a re-delivered event confirms nothing twice.

    _set_status(payment, PaymentStatus.CAPTURED)
    PaymentTransaction.objects.create(
        payment=payment,
        transaction_type=PaymentTransactionType.CAPTURE,
        amount=payment.amount,
        provider_transaction_id=payment.provider_reference,
        success=True,
        raw_response={"note": note} if note else {},
    )

    from apps.checkout.services import confirm_order_after_payment

    confirm_order_after_payment(payment.order)
    return payment


@transaction.atomic
def mark_payment_failed(payment: Payment, *, error_message: str = ""):
    """
    The payment will not complete.

    Refuses to run against a captured payment — that combination means either a
    replayed old event or a bug, and silently overwriting `captured` with
    `failed` would lose the fact that we hold the customer's money.
    """
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.status == PaymentStatus.FAILED:
        return payment

    changed = _set_status(payment, PaymentStatus.FAILED, failure_reason=error_message)
    if not changed:
        return payment

    _record_attempt(payment, PaymentStatus.FAILED, error_message)
    PaymentTransaction.objects.create(
        payment=payment,
        transaction_type=PaymentTransactionType.AUTHORIZE,
        amount=payment.amount,
        success=False,
        raw_response={"error": (error_message or "")[:500]},
    )

    from apps.notifications.tasks import send_email_task

    send_email_task.delay(
        to=payment.order.customer_email,
        subject="Payment failed",
        template_name="payment_failed",
        context={
            "order_number": payment.order.order_number,
            "amount": str(payment.amount.amount),
            "currency": str(payment.amount.currency),
        },
        event="payment_failed",
        user_id=payment.order.user_id,
    )
    return payment


@transaction.atomic
def mark_payment_cancelled(payment: Payment, *, reason: str = ""):
    """Cancel an unsettled payment, voiding it at the gateway where supported."""
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.status == PaymentStatus.CANCELLED:
        return payment

    payment_state.assert_transition(payment.status, PaymentStatus.CANCELLED)
    try:
        provider = get_payment_provider(payment.provider)
        provider.cancel(payment=payment)
    except (NotImplementedError, GatewayError) as exc:
        # The gateway could not be told. Our record still changes: an
        # uncollectable payment must not sit in `pending` forever, and the
        # reason is kept so reconciliation can revisit it.
        logger.info("payment %s: cancel not confirmed at gateway: %s", payment.id, exc)

    _set_status(payment, PaymentStatus.CANCELLED, failure_reason=reason)
    PaymentTransaction.objects.create(
        payment=payment,
        transaction_type=PaymentTransactionType.VOID,
        amount=payment.amount,
        provider_transaction_id=payment.provider_reference,
        success=True,
        raw_response={"reason": (reason or "")[:500]},
    )
    return payment


@transaction.atomic
def mark_payment_expired(payment: Payment, *, reason: str = ""):
    """A Paymob intention or an InstaPay transfer window timed out unpaid."""
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.status == PaymentStatus.EXPIRED:
        return payment
    _set_status(payment, PaymentStatus.EXPIRED, failure_reason=reason)
    return payment


#: Routes settled by a person rather than by a gateway.
#:
#: Cash on delivery (a courier takes cash) and InstaPay (a customer transfers from
#: their banking app). Neither has anything to call, so for both of them the staff
#: member who confirms is the *only* evidence the money arrived — which is why
#: every confirmation here writes an audit entry naming the actor, and why these
#: endpoints are staff-only.
MANUALLY_SETTLED_ROUTES = ("cod", "instapay")

#: Wording per route, so an audit log reads as what actually happened rather than
#: as a generic state change.
_MANUAL_NOTES = {
    "cod": {
        "received": "Cash collected on delivery.",
        "refused": "Refused on delivery.",
        "action": "cod_collection",
    },
    "instapay": {
        "received": "InstaPay transfer confirmed received.",
        "refused": "No InstaPay transfer received.",
        "action": "instapay_confirmation",
    },
}


@transaction.atomic
def record_manual_receipt(payment: Payment, *, actor=None, note: str = ""):
    """
    A person confirms the money arrived: cash at the door, or an InstaPay transfer.

    The one place a manually-settled payment becomes `captured`. Separate from
    `mark_payment_captured` so the audit trail records *who* said so.

    For InstaPay this is the gate on fulfilment: the order is not confirmed until
    this runs, unlike COD where the order ships on a promise. That difference is
    the entire risk distinction between the two methods.
    """
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.provider not in MANUALLY_SETTLED_ROUTES:
        raise ApplicationError(
            ErrorCodes.PAYMENT_INVALID_STATE,
            f"A '{payment.provider}' payment is settled by its gateway, not by hand.",
        )
    if payment.status == PaymentStatus.CAPTURED:
        return payment

    payment_state.assert_transition(payment.status, PaymentStatus.CAPTURED)
    wording = _MANUAL_NOTES[payment.provider]

    result = get_payment_provider(payment.provider).capture(payment=payment)
    if not result.success:
        raise ApplicationError(ErrorCodes.PAYMENT_FAILED, "Could not record the payment.")

    payment = mark_payment_captured(payment, note=note or wording["received"])

    from apps.audit.models import AuditAction
    from apps.audit.services import record

    record(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        model_name="Payment",
        object_id=payment.id,
        changes={
            "status": PaymentStatus.CAPTURED,
            "note": note,
            "method": wording["action"],
        },
    )
    return payment


@transaction.atomic
def record_manual_refusal(payment: Payment, *, actor=None, reason: str = ""):
    """
    The money never arrived: the parcel was refused, or no transfer came.

    Leaves the order alone deliberately. Whether to cancel it, chase the customer
    or re-offer another method is a commercial decision, and this only records
    that the payment did not happen.
    """
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.provider not in MANUALLY_SETTLED_ROUTES:
        raise ApplicationError(
            ErrorCodes.PAYMENT_INVALID_STATE,
            f"A '{payment.provider}' payment is settled by its gateway, not by hand.",
        )
    wording = _MANUAL_NOTES[payment.provider]
    payment = mark_payment_failed(payment, error_message=reason or wording["refused"])

    from apps.audit.models import AuditAction
    from apps.audit.services import record

    record(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        model_name="Payment",
        object_id=payment.id,
        changes={"status": payment.status, "reason": reason, "method": wording["action"]},
    )
    return payment


# Kept as the names the delivery hook and the older tests use. Cash on delivery is
# the common case and reads better with its own verb.
def record_cod_collection(payment: Payment, *, collected_by=None, note: str = ""):
    return record_manual_receipt(payment, actor=collected_by, note=note)


def record_cod_refusal(payment: Payment, *, refused_by=None, reason: str = ""):
    return record_manual_refusal(payment, actor=refused_by, reason=reason)


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


@transaction.atomic
def reconcile_payment(payment: Payment, *, actor=None) -> Payment:
    """
    Ask the gateway what actually happened, and apply it.

    This is the answer to a lost webhook, and it is why a delayed payment is
    recoverable without anybody trusting the customer's browser. It is also why
    `PaymentStatusView` no longer writes `payment.status` itself: the mapping,
    the legality check and the order confirmation all have to happen together,
    and they happen here.
    """
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    try:
        provider = get_payment_provider(payment.provider)
        result = provider.get_status(payment=payment)
    except NotImplementedError as exc:
        logger.info("payment %s: no reconciliation available: %s", payment.id, exc)
        return payment
    except GatewayError as exc:
        logger.warning("payment %s: reconciliation failed: %s", payment.id, exc.detail)
        raise ApplicationError(ErrorCodes.PAYMENT_PROVIDER_ERROR, exc.message) from exc

    try:
        mapped = payment_state.map_provider_status(payment.provider, result.status)
    except KeyError as exc:
        # An unrecognised status is not an excuse to guess.
        logger.warning("payment %s: %s", payment.id, exc)
        return payment

    if mapped is None or mapped == payment.status:
        return payment

    # Verify the gateway is talking about the amount we recorded before we act
    # on what it says. A reconciliation that settles the wrong sum is a silent
    # loss, unlike a webhook mismatch which at least raises.
    if not _amounts_agree(payment, _amount_from_raw(result.raw)):
        logger.error(
            "payment %s: reconciliation amount disagrees with our record; not applying",
            payment.id,
        )
        raise ApplicationError(
            ErrorCodes.PAYMENT_AMOUNT_MISMATCH,
            "The provider reports a different amount for this payment.",
        )

    _apply_mapped_status(payment, mapped, reason="Reconciled with provider.")

    from apps.audit.models import AuditAction
    from apps.audit.services import record

    record(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        model_name="Payment",
        object_id=payment.id,
        changes={"status": mapped, "source": "reconciliation"},
    )
    payment.refresh_from_db()
    return payment


def _amount_from_raw(raw) -> Decimal:
    """
    Pull a comparable amount out of a provider status response, or None.

    Returns None when the provider does not state one, which means "cannot
    check" rather than "matches" — the caller treats None as agreement because
    refusing to reconcile a provider that never quotes amounts would make the
    whole recovery path unusable.
    """
    if not isinstance(raw, dict):
        return None
    if raw.get("amount_cents") is not None:  # Paymob
        try:
            return (Decimal(str(raw["amount_cents"])) / 100).quantize(Decimal("0.01"))
        except (ArithmeticError, ValueError):
            return None
    # Other shapes a future gateway might use. Kept because the cost is nil and
    # the failure mode of not checking an amount is a silent loss.
    for key in ("orderAmount", "paymentAmount"):
        if raw.get(key) is not None:
            try:
                return Decimal(str(raw[key])).quantize(Decimal("0.01"))
            except (ArithmeticError, ValueError):
                return None
    return None


def _amounts_agree(payment: Payment, stated) -> bool:
    if stated is None:
        return True
    return stated == payment.amount.amount.quantize(Decimal("0.01"))


def _apply_mapped_status(payment: Payment, mapped: str, *, reason: str = "") -> None:
    """Route a mapped status to the function that owns that transition."""
    if mapped == PaymentStatus.CAPTURED:
        mark_payment_captured(payment, note=reason)
    elif mapped == PaymentStatus.FAILED:
        mark_payment_failed(payment, error_message=reason)
    elif mapped == PaymentStatus.CANCELLED:
        _set_status(payment, PaymentStatus.CANCELLED, failure_reason=reason)
    elif mapped == PaymentStatus.EXPIRED:
        _set_status(payment, PaymentStatus.EXPIRED, failure_reason=reason)
    else:
        # authorized / requires_action / partially_refunded / refunded.
        _set_status(payment, mapped)


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


def _reject(event: PaymentWebhookEvent, reason: str, *, http_status: int = 202):
    """
    Record a signed event we are not going to act on, and say why.

    Deliberately does not raise. `process_webhook` runs inside a transaction, so
    an exception here would roll back the very row we are trying to keep — the
    first version of this did exactly that, and an amount-mismatch incident left
    no trace at all. The HTTP status travels back on the returned event instead,
    on a transient attribute the view reads.

    `processed` stays False, so rejections are queryable: a run of them is how a
    misconfigured integration announces itself.
    """
    event.processed = False
    event.rejection_reason = reason[:255]
    event.save(update_fields=["processed", "rejection_reason", "payment", "provider_reference"])
    # Transient, not a column: it describes this delivery, not the event.
    event.http_status = http_status
    logger.warning("%s webhook %s rejected: %s", event.provider, event.provider_event_id, reason)
    return event


@transaction.atomic
def process_webhook(*, provider_code: str, headers: dict, body: bytes, query_params: dict) -> PaymentWebhookEvent:
    """
    Apply an inbound provider notification, or refuse to.

    Fails closed at every step. In order: the signature must verify, the event
    must parse, it must not be a duplicate, it must name a payment we hold, the
    amount must match ours, the currency must match ours, the merchant
    reference must be the one we issued, it must not be describing a state older
    than the one we have, and its status must be one we have a mapping for.
    Only then does anything change.
    """
    provider = get_payment_provider(provider_code)

    if not provider.verify_webhook(headers=headers, body=body, query_params=query_params):
        # Nothing is recorded for an unsigned event: the payload is unattributed
        # attacker-controlled data, and storing it by `provider_event_id` would
        # let anyone occupy an id and block the genuine event that follows.
        raise ApplicationError(
            ErrorCodes.PAYMENT_WEBHOOK_INVALID_SIGNATURE,
            "Webhook signature verification failed.",
        )

    try:
        parsed = provider.parse_webhook_event(headers=headers, body=body, query_params=query_params)
    except (ValueError, KeyError, TypeError) as exc:
        raise ApplicationError(
            ErrorCodes.PAYMENT_WEBHOOK_INVALID_SIGNATURE,
            "Webhook payload could not be read.",
        ) from exc

    event_id = str(parsed.get("event_id") or "").strip()
    if not event_id or event_id.endswith(":None"):
        raise ApplicationError(
            ErrorCodes.PAYMENT_WEBHOOK_INVALID_SIGNATURE,
            "Webhook carried no usable event identifier.",
        )

    event, created = PaymentWebhookEvent.objects.get_or_create(
        provider_event_id=event_id,
        defaults={
            "provider": provider_code,
            "event_type": str(parsed.get("event_type") or "")[:100],
            "payload": _redact(parsed.get("payload", {})),
            "signature_valid": True,
            "provider_reference": str(parsed.get("provider_reference") or "")[:150],
            "provider_event_at": parsed.get("event_at"),
        },
    )
    if not created:
        # Replay protection. The provider retries until it gets a 200, so this
        # is the normal path for a second delivery, not an error.
        return event

    event.provider_reference = str(parsed.get("provider_reference") or "")[:150]

    payment = _match_payment(provider_code, parsed)
    event.payment = payment
    if payment is None:
        # Correctly signed but about something we do not hold. Recorded for
        # staff; answered 202 rather than 400 so the provider stops retrying.
        return _reject(event, "No payment matches this event's references.")

    # Lock the payment now: everything below decides its state.
    payment = Payment.objects.select_for_update().get(pk=payment.pk)

    # --- the merchant reference must be the one we issued -----------------
    stated_reference = str(parsed.get("merchant_reference") or "").strip()
    if stated_reference and payment.merchant_reference and stated_reference != payment.merchant_reference:
        # Signed, plausible, and about a different order. 409 because it means
        # two systems disagree about what this reference identifies.
        return _reject(
            event, "Merchant reference does not match the payment.", http_status=409
        )

    # --- the amount must be ours -----------------------------------------
    stated_amount = parsed.get("amount")
    if stated_amount is not None:
        try:
            stated = Decimal(str(stated_amount)).quantize(Decimal("0.01"))
        except (ArithmeticError, ValueError):
            return _reject(event, "Webhook amount could not be read.", http_status=409)
        if stated != payment.amount.amount.quantize(Decimal("0.01")):
            return _reject(
                event,
                f"Webhook amount {stated} does not match the payment's {payment.amount.amount}.",
                http_status=409,
            )

    # --- the currency must be ours ---------------------------------------
    stated_currency = str(parsed.get("currency") or "").strip().upper()
    if stated_currency and stated_currency != str(payment.amount.currency).upper():
        return _reject(
            event,
            f"Webhook currency {stated_currency} does not match the payment's {payment.amount.currency}.",
            http_status=409,
        )

    # --- it must not be stale --------------------------------------------
    event_at = parsed.get("event_at")
    max_age_hours = int(getattr(settings, "PAYMENT_WEBHOOK_MAX_AGE_HOURS", 72))
    if event_at is not None and event_at < timezone.now() - timedelta(hours=max_age_hours):
        return _reject(event, f"Event is older than {max_age_hours}h; not applied.")

    # --- the status must be one we understand -----------------------------
    try:
        mapped = payment_state.map_provider_status(provider_code, parsed.get("status"))
    except KeyError as exc:
        return _reject(event, str(exc)[:255])

    if mapped is None:
        # A real event that carries no state change — an order-created
        # notification, say. Processed, because there is nothing outstanding.
        event.processed = True
        event.processed_at = timezone.now()
        event.save()
        return event

    # --- out-of-order arrival --------------------------------------------
    # Providers do not guarantee order, so a `failed` retry can land after the
    # `captured` that superseded it. The state machine already forbids the
    # illegal move; catching it here turns that into a recorded rejection
    # instead of a 500 and a provider that retries forever.
    if not payment_state.can_transition(payment.status, mapped) and payment.status != mapped:
        return _reject(
            event,
            f"Out-of-order event: cannot move payment from '{payment.status}' to '{mapped}'.",
        )

    _apply_mapped_status(
        payment,
        mapped,
        reason=str(parsed.get("failure_reason") or "")[:500],
    )

    event.processed = True
    event.processed_at = timezone.now()
    event.save()
    return event


def _match_payment(provider_code: str, parsed: dict):
    """
    Find the payment an event is about.

    Tries our own reference first. It is the stronger identifier: we generated
    it, it is unique per payment attempt, and it is the field the provider was
    asked to echo. The provider's own reference is the fallback, because on the
    first callback it may be the only thing that has been assigned.
    """
    merchant_reference = str(parsed.get("merchant_reference") or "").strip()
    if merchant_reference:
        found = Payment.objects.filter(
            merchant_reference=merchant_reference, provider=provider_code
        ).first()
        if found:
            return found

    provider_reference = str(parsed.get("provider_reference") or "").strip()
    if provider_reference:
        return Payment.objects.filter(
            provider_reference=provider_reference, provider=provider_code
        ).first()
    return None


# ---------------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------------


@transaction.atomic
def create_refund(
    *, payment: Payment, amount, reason: str = "", destination=None, idempotency_key: str = "", actor=None
) -> Refund:
    """
    Give money back.

    Capped at what remains of the captured amount, idempotent on the key, and
    only permitted from a state where we actually hold the money. The Refund row
    is written before the gateway is called for the same reason a Payment is:
    if the call times out, a refund may well have happened, and a row is what
    lets somebody find out.
    """
    payment = Payment.objects.select_for_update().get(pk=payment.pk)

    if idempotency_key:
        existing = Refund.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing

    # Refusing to refund an uncaptured payment is the whole point of the state
    # check: before this, a pending COD order could be "refunded" cash that had
    # never been collected.
    if payment.status not in payment_state.REFUNDABLE:
        raise ApplicationError(
            ErrorCodes.PAYMENT_INVALID_STATE,
            f"A payment in state '{payment.status}' holds no money to refund.",
        )

    can_refund, note = payment_config.refund_available(payment.method)
    if not can_refund:
        raise ApplicationError(ErrorCodes.REFUND_NOT_SUPPORTED, note)

    if amount.currency != payment.amount.currency:
        raise ApplicationError(
            ErrorCodes.PAYMENT_CURRENCY_MISMATCH,
            "A refund must be in the same currency as the payment.",
        )
    if amount.amount <= 0:
        raise ApplicationError(ErrorCodes.VALIDATION_ERROR, "A refund must be a positive amount.")

    already_refunded = sum(
        (r.amount for r in payment.refunds.filter(status=RefundStatus.PROCESSED)),
        start=Money(0, payment.amount.currency),
    )
    if already_refunded + amount > payment.amount:
        remaining = payment.amount - already_refunded
        raise ApplicationError(
            ErrorCodes.REFUND_EXCEEDS_CAPTURED_AMOUNT,
            f"Refund exceeds the refundable balance ({remaining} remaining).",
        )

    refund = Refund.objects.create(
        payment=payment,
        order=payment.order,
        amount=amount,
        reason=reason,
        destination=destination or "original_method",
        idempotency_key=idempotency_key,
        status=RefundStatus.PENDING,
    )

    # Visible while the gateway call is in flight, so a refund that never
    # returns is not indistinguishable from one that never started.
    if payment.status in (PaymentStatus.CAPTURED, PaymentStatus.PARTIALLY_REFUNDED):
        _set_status(payment, PaymentStatus.REFUND_PENDING)

    try:
        provider = get_payment_provider(payment.provider)
        result = provider.refund(payment=payment, amount=amount, reason=reason)
    except (NotImplementedError, GatewayError) as exc:
        message = getattr(exc, "message", str(exc))
        refund.status = RefundStatus.FAILED
        refund.save(update_fields=["status"])
        # The money never moved, so the payment goes back to holding it.
        _set_status(payment, PaymentStatus.CAPTURED if already_refunded.amount == 0 else PaymentStatus.PARTIALLY_REFUNDED)
        PaymentTransaction.objects.create(
            payment=payment,
            transaction_type=PaymentTransactionType.REFUND,
            amount=amount,
            success=False,
            raw_response={"error": message[:500]},
        )
        raise ApplicationError(ErrorCodes.PAYMENT_PROVIDER_ERROR, message) from exc

    if result.success:
        refund.status = RefundStatus.PROCESSED
        refund.provider_refund_id = result.provider_refund_id
        refund.processed_at = timezone.now()
        total_refunded = already_refunded + amount
        _set_status(
            payment,
            PaymentStatus.REFUNDED
            if total_refunded >= payment.amount
            else PaymentStatus.PARTIALLY_REFUNDED,
        )
    else:
        refund.status = RefundStatus.FAILED
        _set_status(
            payment,
            PaymentStatus.CAPTURED if already_refunded.amount == 0 else PaymentStatus.PARTIALLY_REFUNDED,
        )
    refund.save()

    PaymentTransaction.objects.create(
        payment=payment,
        transaction_type=PaymentTransactionType.REFUND,
        amount=amount,
        provider_transaction_id=result.provider_refund_id,
        success=result.success,
        raw_response=_redact(result.raw),
    )

    if result.success:
        from apps.notifications.tasks import send_email_task

        send_email_task.delay(
            to=payment.order.customer_email,
            subject="Refund processed",
            template_name="refund",
            context={
                "order_number": payment.order.order_number,
                "amount": str(amount.amount),
                "currency": str(amount.currency),
            },
            event="refund_processed",
            user_id=payment.order.user_id,
        )

    from apps.audit.models import AuditAction
    from apps.audit.services import record

    record(
        actor=actor,
        action=AuditAction.REFUND,
        model_name="Payment",
        object_id=payment.id,
        changes={
            "amount": str(amount),
            "status": refund.status,
            "reason": reason,
            "destination": refund.destination,
            "manual": bool(note),
        },
    )
    return refund
