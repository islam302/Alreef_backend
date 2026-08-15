from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from djmoney.money import Money

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.payments.models import (
    Payment,
    PaymentStatus,
    PaymentTransaction,
    PaymentTransactionType,
    PaymentWebhookEvent,
    Refund,
    RefundStatus,
)
from apps.payments.providers import get_payment_provider


@transaction.atomic
def create_payment_for_order(*, order, provider_code: str, method: str) -> Payment:
    payment = Payment.objects.create(order=order, provider=provider_code, method=method, amount=order.grand_total)
    provider = get_payment_provider(provider_code)
    result = provider.create_payment(payment=payment, order=order)

    if not result.success:
        payment.status = PaymentStatus.FAILED
        payment.save(update_fields=["status"])
        raise ApplicationError(ErrorCodes.PAYMENT_FAILED, result.error or "Payment could not be initiated.")

    payment.provider_reference = result.provider_reference
    payment.status = result.status
    payment.metadata = {**payment.metadata, **result.metadata, "redirect_url": result.redirect_url, "iframe_url": result.iframe_url}
    payment.save(update_fields=["provider_reference", "status", "metadata"])

    PaymentTransaction.objects.create(
        payment=payment, transaction_type=PaymentTransactionType.AUTHORIZE, amount=payment.amount,
        provider_transaction_id=result.provider_reference, success=True, raw_response=_redact(result.raw),
    )
    return payment


@transaction.atomic
def mark_payment_captured(payment: Payment):
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.status == PaymentStatus.CAPTURED:
        return payment
    if payment.status not in (PaymentStatus.PENDING, PaymentStatus.AUTHORIZED):
        raise ApplicationError(ErrorCodes.PAYMENT_ALREADY_CAPTURED, "Payment is not in a capturable state.")

    payment.status = PaymentStatus.CAPTURED
    payment.save(update_fields=["status"])
    PaymentTransaction.objects.create(
        payment=payment, transaction_type=PaymentTransactionType.CAPTURE, amount=payment.amount, success=True,
    )

    from apps.checkout.services import confirm_order_after_payment

    confirm_order_after_payment(payment.order)
    return payment


@transaction.atomic
def mark_payment_failed(payment: Payment, *, error_message: str = ""):
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    payment.status = PaymentStatus.FAILED
    payment.save(update_fields=["status"])
    PaymentTransaction.objects.create(
        payment=payment, transaction_type=PaymentTransactionType.AUTHORIZE, amount=payment.amount,
        success=False, raw_response={"error": error_message},
    )

    from apps.notifications.tasks import send_email_task

    send_email_task.delay(
        to=payment.order.customer_email, subject="Payment failed", template_name="payment_failed",
        context={"order_number": payment.order.order_number, "amount": str(payment.amount.amount), "currency": str(payment.amount.currency)},
        event="payment_failed", user_id=payment.order.user_id,
    )
    return payment


@transaction.atomic
def process_webhook(*, provider_code: str, headers: dict, body: bytes, query_params: dict) -> PaymentWebhookEvent:
    provider = get_payment_provider(provider_code)

    signature_valid = provider.verify_webhook(headers=headers, body=body, query_params=query_params)
    if not signature_valid:
        raise ApplicationError(ErrorCodes.PAYMENT_WEBHOOK_INVALID_SIGNATURE, "Webhook signature verification failed.")

    parsed = provider.parse_webhook_event(headers=headers, body=body, query_params=query_params)

    event, created = PaymentWebhookEvent.objects.get_or_create(
        provider_event_id=parsed["event_id"],
        defaults={
            "provider": provider_code, "event_type": parsed.get("event_type", ""), "payload": _redact(parsed.get("payload", {})),
            "signature_valid": signature_valid,
        },
    )
    if not created:
        return event  # Idempotent: already processed this exact event (spec §42/§89 replay protection).

    payment = Payment.objects.filter(provider_reference=parsed.get("provider_reference", "")).select_for_update().first()
    event.payment = payment
    if payment:
        # Never trust a captured/failed status against an amount the gateway
        # claims if it disagrees with our own authoritative payment.amount
        # (fixed server-side at creation from order.grand_total) — a forged
        # or misrouted webhook could otherwise "confirm" a captured amount
        # the merchant never actually received.
        webhook_amount = parsed.get("amount")
        if webhook_amount is not None and Decimal(str(webhook_amount)) != payment.amount.amount:
            event.processed = False
            event.save(update_fields=["payment", "processed"])
            raise ApplicationError(
                ErrorCodes.PAYMENT_AMOUNT_MISMATCH,
                "Webhook-reported amount does not match the payment's authoritative amount.",
            )

        status = parsed.get("status")
        if status == "captured":
            mark_payment_captured(payment)
        elif status == "failed":
            mark_payment_failed(payment)
    event.processed = True
    event.processed_at = timezone.now()
    event.save()
    return event


@transaction.atomic
def create_refund(*, payment: Payment, amount, reason: str = "", destination=None, idempotency_key: str = "") -> Refund:
    payment = Payment.objects.select_for_update().get(pk=payment.pk)

    if idempotency_key:
        existing = Refund.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing

    already_refunded = sum(
        (r.amount for r in payment.refunds.filter(status=RefundStatus.PROCESSED)),
        start=Money(0, payment.amount.currency),
    )
    if already_refunded + amount > payment.amount:
        raise ApplicationError(ErrorCodes.REFUND_EXCEEDS_CAPTURED_AMOUNT, "Refund amount exceeds the captured payment amount.")

    refund = Refund.objects.create(
        payment=payment, order=payment.order, amount=amount, reason=reason,
        destination=destination or "original_method", idempotency_key=idempotency_key,
    )

    provider = get_payment_provider(payment.provider)
    result = provider.refund(payment=payment, amount=amount, reason=reason)

    if result.success:
        refund.status = RefundStatus.PROCESSED
        refund.provider_refund_id = result.provider_refund_id
        refund.processed_at = timezone.now()
        payment.status = PaymentStatus.PARTIALLY_REFUNDED if already_refunded + amount < payment.amount else PaymentStatus.REFUNDED
        payment.save(update_fields=["status"])
    else:
        refund.status = RefundStatus.FAILED
    refund.save()

    PaymentTransaction.objects.create(
        payment=payment, transaction_type=PaymentTransactionType.REFUND, amount=amount,
        provider_transaction_id=result.provider_refund_id, success=result.success, raw_response=_redact(result.raw),
    )

    from apps.notifications.tasks import send_email_task

    if result.success:
        send_email_task.delay(
            to=payment.order.customer_email, subject="Refund processed", template_name="refund",
            context={"order_number": payment.order.order_number, "amount": str(amount.amount), "currency": str(amount.currency)},
            event="refund_processed", user_id=payment.order.user_id,
        )

    from apps.audit.models import AuditAction
    from apps.audit.services import record

    record(
        action=AuditAction.REFUND, model_name="Payment", object_id=payment.id,
        changes={"amount": str(amount), "status": refund.status, "reason": reason},
    )
    return refund


def _redact(raw):
    from apps.core.logging_filters import redact

    if raw is None:
        return {}
    if isinstance(raw, dict):
        return redact(raw)
    return {"raw": str(raw)}
