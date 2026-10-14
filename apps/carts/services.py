from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.carts.models import Cart, CartItem, CartStatus
from apps.catalog.models import ProductVariant
from apps.core.error_codes import ApplicationError, ErrorCodes


def get_or_create_cart(request) -> Cart:
    if request.user.is_authenticated:
        cart, _ = Cart.objects.get_or_create(user=request.user, status=CartStatus.OPEN, defaults={})
        return cart

    if not request.session.session_key:
        request.session.save()
    session_key = request.session.session_key
    cart, _ = Cart.objects.get_or_create(session_key=session_key, user=None, status=CartStatus.OPEN, defaults={})
    return cart


@transaction.atomic
def add_item(cart: Cart, *, variant_id, quantity: int) -> CartItem:
    variant = ProductVariant.objects.filter(pk=variant_id, is_active=True).select_related("product").first()
    if not variant:
        raise ApplicationError(ErrorCodes.VARIANT_NOT_FOUND, "Product variant not found or inactive.")

    item, created = CartItem.objects.get_or_create(
        cart=cart, variant=variant, defaults={"quantity": quantity, "unit_price_snapshot": variant.price}
    )
    if not created:
        # The serializer caps each request, not the line: without this, three
        # adds of the maximum built a line three times over the ceiling.
        if item.quantity + quantity > settings.MAX_CART_ITEM_QUANTITY:
            raise ApplicationError(
                ErrorCodes.VALIDATION_ERROR,
                f"At most {settings.MAX_CART_ITEM_QUANTITY} of the same item per order. "
                f"Please contact us for a larger quantity.",
            )
        item.quantity += quantity
        item.unit_price_snapshot = variant.price
        item.save(update_fields=["quantity", "unit_price_snapshot"])
    cart.save(update_fields=["last_activity_at"])
    return item


@transaction.atomic
def update_item_quantity(cart: Cart, *, item_id, quantity: int) -> CartItem:
    item = CartItem.objects.filter(cart=cart, pk=item_id).select_related("variant").first()
    if not item:
        raise ApplicationError(ErrorCodes.CART_ITEM_NOT_FOUND, "Cart item not found.")
    if quantity <= 0:
        item.delete()
        return None
    item.quantity = quantity
    item.unit_price_snapshot = item.variant.price
    item.save(update_fields=["quantity", "unit_price_snapshot"])
    cart.save(update_fields=["last_activity_at"])
    return item


@transaction.atomic
def remove_item(cart: Cart, *, item_id):
    deleted, _ = CartItem.objects.filter(cart=cart, pk=item_id).delete()
    if not deleted:
        raise ApplicationError(ErrorCodes.CART_ITEM_NOT_FOUND, "Cart item not found.")
    cart.save(update_fields=["last_activity_at"])


@transaction.atomic
def clear_cart(cart: Cart):
    cart.items.all().delete()
    cart.coupon_code = ""
    cart.save(update_fields=["coupon_code", "last_activity_at"])


@transaction.atomic
def merge_guest_cart_into_user(*, guest_cart: Cart, user) -> Cart:
    """
    Conflict rule (spec §24): same variant in both carts -> quantities are
    summed. The user's existing OPEN cart (if any) is the merge target; the
    guest cart is marked MERGED and its items removed.
    """
    if guest_cart.user_id == user.id:
        return guest_cart

    user_cart, _ = Cart.objects.select_for_update().get_or_create(user=user, status=CartStatus.OPEN, defaults={})

    for guest_item in guest_cart.items.select_related("variant").all():
        existing = user_cart.items.filter(variant=guest_item.variant).first()
        if existing:
            existing.quantity = min(existing.quantity + guest_item.quantity, settings.MAX_CART_ITEM_QUANTITY)
            existing.unit_price_snapshot = guest_item.variant.price
            existing.save(update_fields=["quantity", "unit_price_snapshot"])
        else:
            CartItem.objects.create(
                cart=user_cart, variant=guest_item.variant, quantity=guest_item.quantity,
                unit_price_snapshot=guest_item.variant.price,
            )

    if user_cart.coupon_code == "" and guest_cart.coupon_code:
        user_cart.coupon_code = guest_cart.coupon_code

    guest_cart.status = CartStatus.MERGED
    guest_cart.items.all().delete()
    guest_cart.save(update_fields=["status"])
    user_cart.save(update_fields=["coupon_code", "last_activity_at"])
    return user_cart


def get_cart_subtotal(cart: Cart):
    from djmoney.money import Money

    items = list(cart.items.select_related("variant").all())
    if not items:
        return Money(0, cart.currency)
    total = sum((item.line_total for item in items), start=Money(0, cart.currency))
    return total


def get_abandoned_carts():
    threshold = timezone.now() - timedelta(hours=settings.ABANDONED_CART_HOURS)
    return Cart.objects.filter(
        status=CartStatus.OPEN, last_activity_at__lt=threshold, abandoned_notified_at__isnull=True, items__isnull=False
    ).distinct().select_related("user")
