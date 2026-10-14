from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.exceptions import NotAuthenticated
from rest_framework.views import APIView

from apps.carts.services import get_or_create_cart
from apps.checkout.serializers import (
    CheckoutInputSerializer,
    CheckoutResultSerializer,
    CheckoutTotalsSerializer,
    GuestCheckoutInputSerializer,
    GuestCheckoutSummarySerializer,
)
from apps.checkout.services import calculate_totals, create_order_from_cart
from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.core.idempotency import IdempotentRequestMixin
from apps.core.response import success_response
from apps.orders.serializers import OrderDetailSerializer
from apps.users.models import Address


def _get_address(user, address_id):
    address = Address.objects.filter(pk=address_id, user=user).select_related("governorate", "city").first()
    if not address:
        raise ApplicationError(ErrorCodes.ADDRESS_NOT_FOUND, "Address not found.")
    return address


def _guest_checkout_allowed(request):
    """
    Signed-in customers take the account path; everyone else may check out as a
    guest unless the shop has switched that off, in which case the endpoint
    answers exactly as it did before guest checkout existed.

    A guest never gets to name a saved address. Address ids are sequential, and
    an anonymous request that could pass one would ship to — and read back the
    name and phone on — somebody else's address book.
    """
    if not getattr(settings, "GUEST_CHECKOUT_ENABLED", True):
        raise NotAuthenticated()
    if "address_id" in request.data:
        raise ApplicationError(ErrorCodes.ADDRESS_NOT_FOUND, "Address not found.")


@extend_schema(tags=["Checkout"], request=CheckoutInputSerializer, responses=CheckoutTotalsSerializer)
class CheckoutSummaryView(APIView):
    """
    Preview order totals before committing — the backend, never the frontend,
    computes these (spec §36).

    Signed in: `address_id` from the address book. Guest: `governorate` (id),
    plus the `phone` when known so a coupon's per-customer limit is checked.
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        cart = get_or_create_cart(request)

        if request.user.is_authenticated:
            serializer = CheckoutInputSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data
            address = _get_address(request.user, data["address_id"])
            user, phone = request.user, ""
        else:
            _guest_checkout_allowed(request)
            serializer = GuestCheckoutSummarySerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data
            address = serializer.build_address()
            user, phone = None, str(data.get("phone") or "")

        totals = calculate_totals(
            cart=cart, address=address, shipping_method_id=data["shipping_method_id"],
            payment_method=data["payment_method"], coupon_code=data.get("coupon_code", ""), user=user, phone=phone,
        )
        return success_response(CheckoutTotalsSerializer(totals).data)


@extend_schema(tags=["Checkout"], request=CheckoutInputSerializer, responses=CheckoutResultSerializer)
class CheckoutCreateView(IdempotentRequestMixin, APIView):
    """
    Creates the Order from the current cart (spec §64). Requires an
    `Idempotency-Key` header so retried requests never double-place an order.

    Open to guests: without a session the cart is the one on the session cookie,
    the body carries `contact` and an inline `address` instead of `address_id`,
    and the response adds an `access_token` — the only way back to the order
    without an account (apps.orders.guest). Same cart lock, same pricing, same
    coupon rules, same throttle.
    """

    permission_classes = [permissions.AllowAny]
    idempotency_scope = "checkout"
    throttle_scope = "checkout"

    def handle_idempotent_post(self, request, *args, **kwargs):
        cart = get_or_create_cart(request)

        if request.user.is_authenticated:
            serializer = CheckoutInputSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data
            address = _get_address(request.user, data["address_id"])
            user, contact_email = request.user, ""
        else:
            _guest_checkout_allowed(request)
            serializer = GuestCheckoutInputSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data
            address = serializer.build_address()
            user, contact_email = None, data["contact"].get("email", "")

        order, payment = create_order_from_cart(
            user=user, cart=cart, address=address, shipping_method_id=data["shipping_method_id"],
            payment_method=data["payment_method"], coupon_code=data.get("coupon_code", ""), notes=data.get("notes", ""),
            # Carried through to the Payment, where a unique constraint makes a
            # retry that outlives the cache still unable to charge twice.
            idempotency_key=request.headers.get("Idempotency-Key", ""),
            contact_email=contact_email,
        )

        # Deliberately narrow. The storefront gets what it needs to send the
        # customer onward and nothing else: no provider payload, no reference it
        # has no use for, and never anything that would let it decide the order
        # is paid. That verdict comes from GET /payments/{id}/status/.
        payment_data = {
            "id": payment.id,
            "provider": payment.provider,
            "method": payment.method,
            "status": payment.status,
            "redirect_url": payment.metadata.get("redirect_url", ""),
            "iframe_url": payment.metadata.get("iframe_url", ""),
            "provider_reference": payment.provider_reference,
            # Shown to the customer: the sentence telling them what to do, and
            # for a transfer, where to send it and what to quote. All published
            # information — an InstaPay address is a payment address, not a secret.
            "instructions_ar": payment.metadata.get("instructions_ar", ""),
            "pay_to": payment.metadata.get("instapay_address", ""),
            "pay_reference": payment.metadata.get("transfer_reference", order.order_number),
        }
        body = {"order": OrderDetailSerializer(order).data, "payment": payment_data}
        if order.user_id is None:
            from apps.orders.guest import issue_access_token

            body["access_token"] = issue_access_token(order)
        return success_response(body, message="Order created.", status=201)
