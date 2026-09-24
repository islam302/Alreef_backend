import logging

from django.http import HttpResponse
from djmoney.money import Money
from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.generics import RetrieveAPIView
from rest_framework.views import APIView

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.core.idempotency import IdempotentRequestMixin
from apps.core.response import success_response
from apps.payments import config as payment_config
from apps.payments import services
from apps.payments.models import Payment
from apps.payments.serializers import (
    ManualSettlementSerializer,
    PaymentMethodSerializer,
    PaymentSerializer,
    RefundCreateSerializer,
    RefundSerializer,
)
from apps.payments.webhooks import WEBHOOK_PROVIDERS

logger = logging.getLogger(__name__)


@extend_schema(tags=["Payments"], responses=PaymentMethodSerializer(many=True))
class PaymentMethodsView(APIView):
    """
    GET /api/v1/payments/methods/

    The payment methods this installation can actually complete, in display
    order. Public, because the storefront needs it before anybody signs in, and
    unpaginated because there are at most a handful.

    This replaced a hard-coded list in the storefront bundle. Two copies of
    "which gateways work" drift, and they drift the dangerous way: the shop keeps
    offering a method after its credentials are pulled, and the customer only
    finds out once they have entered an address.

    Says nothing about *why* a method is missing. That names our configuration,
    so it goes to staff through the dashboard and to the operator through
    `manage.py check_payments` — never to the public.
    """

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        specs = payment_config.enabled_methods()
        return success_response(PaymentMethodSerializer(specs, many=True).data)


@extend_schema(tags=["Payments"])
class PaymentDetailView(RetrieveAPIView):
    serializer_class = PaymentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # The filter, not the permission class, is what keeps one customer out
        # of another's payment: IsAuthenticated is satisfied by any account.
        return Payment.objects.filter(order__user=self.request.user)

    def retrieve(self, request, *args, **kwargs):
        return success_response(self.get_serializer(self.get_object()).data)


@extend_schema(tags=["Payments"])
class PaymentStatusView(APIView):
    """
    GET /api/v1/payments/{id}/status/

    What the storefront polls after a customer comes back from a gateway. This
    is the only thing that may tell the browser an order is paid, and the
    browser can never tell it the reverse.

    It used to assign whatever string a provider's status endpoint returned
    straight onto `payment.status` — skipping the state machine, and, when the
    answer was "captured", never confirming the order. So a customer whose
    webhook was lost saw a paid payment attached to an unconfirmed order with
    stock still merely reserved. Reconciliation now happens in
    `services.reconcile_payment`, which maps, checks the amount, checks the
    transition is legal and confirms the order as one unit.
    """

    permission_classes = [permissions.IsAuthenticated]
    # Polled every couple of seconds by a waiting customer, and each call can
    # reach a gateway, so it gets its own budget rather than the generic one.
    throttle_scope = "payment_status"

    def get(self, request, pk):
        payment = Payment.objects.filter(pk=pk, order__user=request.user).first()
        if not payment:
            raise ApplicationError(ErrorCodes.NOT_FOUND, "Payment not found.")

        try:
            payment = services.reconcile_payment(payment)
        except ApplicationError as exc:
            # A gateway that is down or disagreeing must not break the page the
            # customer is waiting on. They see the status we already hold, which
            # is the honest answer: we do not yet know.
            logger.warning("payment %s: status poll could not reconcile: %s", payment.id, exc)

        return success_response(PaymentSerializer(payment).data)


@extend_schema(tags=["Webhooks"])
class PaymentWebhookView(APIView):
    """
    POST /api/v1/payments/webhooks/{provider}/

    Public and unauthenticated by necessity — the caller is Paymob, not a user —
    so the signature is the only thing standing between this endpoint and an
    attacker who can forge paid orders. It is verified before the payload is
    parsed, and nothing is recorded for an event that fails it.

    Status codes are chosen for how a provider retries:
    * 200 — applied, or a duplicate we have already applied. Stop retrying.
    * 202 — signed and recorded, but not applied (unknown reference, stale
      event, a status we have no mapping for). Also stop retrying: repeating it
      will not change our answer, and a provider hammering us for days over an
      event about somebody else's account is noise, not recovery.
    * 400 — the signature did not verify, or the payload was unreadable.
    * 409 — the event contradicts our record on amount or currency. This is the
      one worth alerting on.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_scope = "payment_webhook"

    def post(self, request, provider):
        # Only gateways that actually send webhooks. `cod` is excluded on
        # purpose: it has none, so any POST to it can only be hostile.
        if provider not in WEBHOOK_PROVIDERS:
            raise ApplicationError(ErrorCodes.NOT_FOUND, "Unknown payment provider.")

        try:
            event = services.process_webhook(
                provider_code=provider,
                headers=dict(request.headers),
                body=request.body,
                # `.dict()`, not `dict(...)`. A QueryDict is a MultiValueDict, so
                # `dict(qd)` gives {"hmac": ["abc..."]} — a list, which stringifies
                # to "['abc...']" and never matches a digest. Paymob signs its
                # callbacks with an `hmac` query parameter, so that one line was
                # enough to make every genuine Paymob webhook fail verification.
                query_params=request.query_params.dict(),
            )
        except NotImplementedError as exc:
            # The gateway is switched off at our end. Accepted so the provider
            # stops retrying, and logged loudly, because a live provider calling
            # a disabled integration means the two ends disagree.
            logger.error("Webhook received for disabled provider '%s': %s", provider, exc)
            return HttpResponse(status=202)

        # `http_status` is set by services._reject for a signed event we
        # refused; otherwise 200 for applied (and for duplicates, which are
        # already applied) and 202 for recorded-but-not-applied.
        return HttpResponse(
            status=getattr(event, "http_status", 200 if event.processed else 202)
        )


@extend_schema(tags=["Payments"], request=RefundCreateSerializer)
class RefundCreateView(IdempotentRequestMixin, APIView):
    """Staff-only. Amount-capped and audited in `services.create_refund`."""

    permission_classes = [permissions.IsAdminUser]
    idempotency_scope = "refund"

    def handle_idempotent_post(self, request, *args, **kwargs):
        payment = Payment.objects.filter(pk=kwargs["pk"]).first()
        if not payment:
            raise ApplicationError(ErrorCodes.NOT_FOUND, "Payment not found.")

        serializer = RefundCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        refund = services.create_refund(
            payment=payment,
            # The currency is the payment's, never the caller's. A refund in a
            # currency the customer did not pay in is not a refund.
            amount=Money(data["amount"], payment.amount.currency),
            reason=data["reason"],
            destination=data["destination"],
            idempotency_key=request.headers.get("Idempotency-Key", ""),
            actor=request.user,
        )
        return success_response(RefundSerializer(refund).data, message="Refund recorded.", status=201)


@extend_schema(tags=["Payments"])
class PaymentReconcileView(APIView):
    """
    POST /api/v1/payments/{id}/reconcile/

    Staff-triggered "ask the gateway what really happened". The manual
    counterpart to a lost webhook, and the tool for the case that otherwise
    needs a database console: a payment stuck in `requires_action` that the
    customer insists they completed.
    """

    permission_classes = [permissions.IsAdminUser]

    def post(self, request, pk):
        payment = Payment.objects.filter(pk=pk).first()
        if not payment:
            raise ApplicationError(ErrorCodes.NOT_FOUND, "Payment not found.")
        payment = services.reconcile_payment(payment, actor=request.user)
        return success_response(PaymentSerializer(payment).data, message="Reconciled with the provider.")


@extend_schema(tags=["Payments"], request=ManualSettlementSerializer)
class ManualSettlementView(APIView):
    """
    POST /api/v1/payments/{id}/settle/

    Staff confirming that a manually-settled payment arrived, or did not.

    Two methods are settled this way, for the same reason — there is no gateway to
    ask — but they carry very different risk:

    * **Cash on delivery.** Usually implicit: moving an order to DELIVERED
      captures its payment. This endpoint is for the exceptions — cash taken
      before the status caught up, or a parcel refused at the door.
    * **InstaPay.** *Always* explicit, and this is the gate on fulfilment. An
      InstaPay order is not confirmed until somebody checks the shop's InstaPay
      app and posts `received` here. Nothing else will ever confirm it.

    Staff-only, and audited with the actor's identity, because for these methods
    that identity is the only evidence the money arrived.
    """

    permission_classes = [permissions.IsAdminUser]

    def post(self, request, pk):
        payment = Payment.objects.filter(pk=pk).first()
        if not payment:
            raise ApplicationError(ErrorCodes.NOT_FOUND, "Payment not found.")

        serializer = ManualSettlementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        outcome = serializer.validated_data["outcome"]
        note = serializer.validated_data.get("note", "")

        if outcome == "received":
            payment = services.record_manual_receipt(payment, actor=request.user, note=note)
            message = "Payment recorded as received."
        else:
            payment = services.record_manual_refusal(payment, actor=request.user, reason=note)
            message = "Recorded that no payment arrived."

        return success_response(PaymentSerializer(payment).data, message=message)
