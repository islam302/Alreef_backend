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
from apps.payments import services
from apps.payments.models import Payment, PaymentProviderCode
from apps.payments.providers import get_payment_provider
from apps.payments.serializers import PaymentSerializer, RefundCreateSerializer, RefundSerializer

logger = logging.getLogger(__name__)


@extend_schema(tags=["Payments"])
class PaymentDetailView(RetrieveAPIView):
    serializer_class = PaymentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Payment.objects.filter(order__user=self.request.user)

    def retrieve(self, request, *args, **kwargs):
        return success_response(self.get_serializer(self.get_object()).data)


@extend_schema(tags=["Payments"])
class PaymentStatusView(APIView):
    """Frontend polls this after redirect back from a gateway (spec §64/§65).
    The frontend must never mark a payment successful itself — only this
    server-verified status is authoritative."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        payment = Payment.objects.filter(pk=pk, order__user=request.user).first()
        if not payment:
            raise ApplicationError(ErrorCodes.NOT_FOUND, "Payment not found.")
        try:
            provider = get_payment_provider(payment.provider)
            result = provider.get_status(payment=payment)
            if result.status != payment.status:
                payment.status = result.status
                payment.save(update_fields=["status"])
        except NotImplementedError:
            pass  # Provider not wired for live status polling yet — rely on webhooks.
        return success_response(PaymentSerializer(payment).data)


@extend_schema(tags=["Webhooks"])
class PaymentWebhookView(APIView):
    """Public inbound endpoint per provider: /api/v1/payments/webhooks/{provider}/.
    Never exposes webhook secrets; signature is verified server-side (spec §85)."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_scope = "payment_webhook"

    def post(self, request, provider):
        if provider not in PaymentProviderCode.values:
            raise ApplicationError(ErrorCodes.NOT_FOUND, "Unknown payment provider.")
        try:
            services.process_webhook(
                provider_code=provider, headers=dict(request.headers), body=request.body, query_params=dict(request.query_params),
            )
        except NotImplementedError as exc:
            logger.warning("Webhook received for unconfigured provider '%s': %s", provider, exc)
            return HttpResponse(status=202)
        return HttpResponse(status=200)


@extend_schema(tags=["Payments"], request=RefundCreateSerializer)
class RefundCreateView(IdempotentRequestMixin, APIView):
    permission_classes = [permissions.IsAdminUser]
    idempotency_scope = "refund"

    def handle_idempotent_post(self, request, *args, **kwargs):
        payment_id = kwargs["pk"]
        payment = Payment.objects.filter(pk=payment_id).first()
        if not payment:
            raise ApplicationError(ErrorCodes.NOT_FOUND, "Payment not found.")

        serializer = RefundCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        refund = services.create_refund(
            payment=payment, amount=Money(data["amount"], payment.amount.currency), reason=data["reason"],
            destination=data["destination"], idempotency_key=request.headers.get("Idempotency-Key", ""),
        )
        return success_response(RefundSerializer(refund).data, message="Refund processed.", status=201)
