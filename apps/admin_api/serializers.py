"""
Staff-facing serializers.

These deliberately expose more than the storefront equivalents (cost price,
internal notes, customer contact details, audit metadata) because every
route that uses them is gated behind `IsAdminUser`.
"""
import re

from rest_framework import serializers

from apps.audit.models import AuditLog
from apps.catalog.models import Product, ProductImage, ProductVariant
from apps.categories.models import Category
from apps.coupons.models import Coupon
from apps.inventory.models import Batch, InventoryItem, InventoryMovement
from apps.orders.models import Order, OrderItem, OrderStatusHistory, ReturnRequest
from apps.payments.models import Payment, Refund
from apps.reviews.models import Review, StoreReview
from apps.shipping.models import (
    Governorate,
    Shipment,
    ShipmentEvent,
    ShipmentStatus,
    ShippingMethod,
    ShippingRate,
    ShippingZone,
)
from apps.users.models import User


# --------------------------------------------------------------------------
# Orders
# --------------------------------------------------------------------------
class AdminOrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = [
            "id", "product", "variant", "product_name_snapshot", "variant_name_snapshot",
            "sku_snapshot", "unit_price", "quantity", "discount_amount", "tax_amount", "line_total",
        ]


class AdminOrderStatusHistorySerializer(serializers.ModelSerializer):
    changed_by_name = serializers.CharField(source="changed_by.full_name", default="", read_only=True)

    class Meta:
        model = OrderStatusHistory
        fields = ["id", "from_status", "to_status", "changed_by", "changed_by_name", "note", "created_at"]


class AdminOrderListSerializer(serializers.ModelSerializer):
    """Index row - intentionally flat and cheap; no nested items."""

    item_count = serializers.IntegerField(read_only=True, default=0)
    is_cod = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id", "order_number", "status", "customer_name", "customer_phone", "customer_email",
            "payment_method", "is_cod", "grand_total", "currency", "item_count", "created_at",
        ]

    def get_is_cod(self, obj) -> bool:
        return obj.payment_method == "cod"


#: Metadata keys an operator may see. Everything else on `Payment.metadata` is
#: withheld — not because it is necessarily secret, but because an allowlist is
#: the only version of this that stays safe when an adapter starts storing
#: something new. A denylist would leak the next field somebody adds.
SAFE_PAYMENT_METADATA_KEYS = (
    # InstaPay: where the customer was told to transfer, and what to quote.
    "instapay_address",
    "instapay_account_name",
    "transfer_amount",
    "transfer_reference",
    "confirm_window_hours",
    "intention_id",
    "intention_order_id",
    "integration_id",
    "instructions_ar",
    "collect_amount",
    # Money we hold but owe back (paid after a cancel, or charged twice), and
    # the extra gateway transactions behind it. Shown so staff refund it.
    "needs_refund",
    "needs_refund_reason",
    "duplicate_charges",
    "captured_transaction",
    "currency",
    "live",
    "expires_at",
)


class AdminPaymentSerializer(serializers.ModelSerializer):
    """
    What staff need to reconcile a payment, and nothing they do not.

    Carries the provider reference and our own merchant reference (the two things
    you search a provider's dashboard by), the failure reason, the refund history
    and the gateway round-trips. It deliberately does not carry the raw provider
    payload: those contain HMACs and card metadata, and an operator's browser is
    not where either belongs.
    """

    order_number = serializers.CharField(source="order.order_number", read_only=True)
    refunds = serializers.SerializerMethodField()
    transactions = serializers.SerializerMethodField()
    #: Whether a refund can actually be issued, and if not, why. Lets the
    #: dashboard disable the button with a reason instead of offering one that
    #: always fails.
    refundable = serializers.SerializerMethodField()
    refund_note = serializers.SerializerMethodField()
    refundable_amount = serializers.SerializerMethodField()
    #: Whether this gateway can be asked what really happened.
    reconcilable = serializers.SerializerMethodField()
    metadata = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            "id", "order", "order_number", "provider", "method", "amount", "status",
            "provider_reference", "merchant_reference", "failure_reason", "expires_at",
            "metadata", "refunds", "transactions", "refundable", "refund_note",
            "refundable_amount", "reconcilable", "created_at", "updated_at",
        ]

    def get_refunds(self, obj) -> list:
        return AdminRefundSerializer(obj.refunds.all(), many=True).data

    def get_transactions(self, obj) -> list:
        """
        The gateway round-trips, without their bodies.

        `PaymentTransaction.raw_response` is redacted before storage, but it is
        still a provider payload; the dashboard gets the shape of the history
        (what was attempted, when, and whether it worked) rather than the
        contents.
        """
        return [
            {
                "type": txn.transaction_type,
                "amount": str(txn.amount.amount),
                "currency": str(txn.amount.currency),
                "success": txn.success,
                "provider_transaction_id": txn.provider_transaction_id,
                "created_at": txn.created_at,
            }
            for txn in obj.transactions.all()
        ]

    def get_refundable(self, obj) -> bool:
        from apps.payments import config as payment_config
        from apps.payments import state as payment_state

        if obj.status not in payment_state.REFUNDABLE:
            return False
        can_refund, _note = payment_config.refund_available(obj.method)
        return can_refund

    def get_refund_note(self, obj) -> str:
        from apps.payments import config as payment_config
        from apps.payments import state as payment_state

        if obj.status not in payment_state.REFUNDABLE:
            return f"A payment in state '{obj.status}' holds no money to refund."
        _can_refund, note = payment_config.refund_available(obj.method)
        return note

    def get_refundable_amount(self, obj) -> str:
        """What is left. Stops staff from having to do the subtraction."""
        from djmoney.money import Money

        from apps.payments.models import RefundStatus

        already = sum(
            (r.amount for r in obj.refunds.all() if r.status == RefundStatus.PROCESSED),
            start=Money(0, obj.amount.currency),
        )
        return str((obj.amount - already).amount)

    def get_reconcilable(self, obj) -> bool:
        from apps.payments.webhooks import WEBHOOK_PROVIDERS

        return obj.provider in WEBHOOK_PROVIDERS

    def get_metadata(self, obj) -> dict:
        return {
            key: value
            for key, value in (obj.metadata or {}).items()
            if key in SAFE_PAYMENT_METADATA_KEYS
        }


class AdminRefundSerializer(serializers.ModelSerializer):
    class Meta:
        model = Refund
        fields = ["id", "payment", "order", "amount", "reason", "destination", "status", "processed_at", "created_at"]


class AdminOrderDetailSerializer(serializers.ModelSerializer):
    items = AdminOrderItemSerializer(many=True, read_only=True)
    status_history = AdminOrderStatusHistorySerializer(many=True, read_only=True)
    allowed_transitions = serializers.SerializerMethodField()
    payments = AdminPaymentSerializer(many=True, read_only=True)
    shipments = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id", "order_number", "status", "allowed_transitions", "currency",
            "user", "customer_name", "customer_email", "customer_phone",
            "shipping_address_snapshot", "shipping_method_name", "payment_method",
            "subtotal", "coupon_code", "coupon_discount", "promotion_discount", "loyalty_discount",
            "gift_card_amount_used", "store_credit_amount_used", "tax_total", "shipping_total",
            "cod_fee", "grand_total", "notes", "cancel_reason",
            "confirmed_at", "shipped_at", "delivered_at", "cancelled_at", "created_at", "updated_at",
            "items", "status_history", "payments", "shipments",
        ]

    def get_shipments(self, obj) -> list:
        # A method field because the shipping serializers are declared further
        # down this module.
        return AdminShipmentSerializer(obj.shipments.all(), many=True).data

    def get_allowed_transitions(self, obj) -> list:
        from apps.orders.models import ORDER_STATUS_TRANSITIONS

        return sorted(str(s) for s in ORDER_STATUS_TRANSITIONS.get(obj.status, set()))


class OrderStatusChangeSerializer(serializers.Serializer):
    status = serializers.CharField()
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def validate_status(self, value):
        from apps.orders.models import OrderStatus

        if value not in OrderStatus.values:
            raise serializers.ValidationError(f"Unknown status '{value}'.")
        return value


class OrderCancelAdminSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class OrderNoteSerializer(serializers.Serializer):
    notes = serializers.CharField(max_length=500, allow_blank=True)


# --------------------------------------------------------------------------
# Returns
# --------------------------------------------------------------------------
class AdminReturnRequestSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.order_number", read_only=True)
    customer_name = serializers.CharField(source="order.customer_name", read_only=True)
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = ReturnRequest
        fields = [
            "id", "order", "order_number", "customer_name", "user", "reason", "description",
            "status", "resolution_note", "item_count", "created_at",
        ]

    def get_item_count(self, obj) -> int:
        return obj.items.count()


class ReturnDecisionSerializer(serializers.Serializer):
    approve = serializers.BooleanField()
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------
class AdminProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ["id", "product", "variant", "image", "alt_text", "is_primary", "sort_order"]
        read_only_fields = ["product"]


def _stock_payload(item) -> dict:
    if item is None:
        return {"available_quantity": 0, "quantity_on_hand": 0, "reserved_quantity": 0,
                "is_low_stock": False, "is_out_of_stock": True, "tracked": False}
    return {
        "available_quantity": item.available_quantity,
        "quantity_on_hand": item.quantity_on_hand,
        "reserved_quantity": item.reserved_quantity,
        "is_low_stock": item.is_low_stock,
        "is_out_of_stock": item.is_out_of_stock,
        "tracked": item.track_inventory,
    }


class AdminProductVariantSerializer(serializers.ModelSerializer):
    stock = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = [
            "id", "product", "sku", "barcode", "name_suffix", "price", "compare_at_price",
            "cost_price", "weight_grams", "is_active", "is_default", "sort_order", "stock",
        ]
        read_only_fields = ["product"]

    def get_stock(self, obj) -> dict:
        return _stock_payload(getattr(obj, "inventory_item", None))


class _OfferPriceMixin:
    def validate(self, attrs):
        price, was = attrs.get("price"), attrs.get("compare_at_price")
        if was is not None and price is not None and was <= price:
            raise serializers.ValidationError(
                {"compare_at_price": "The price before the discount must be higher than the price."}
            )
        return attrs


class OfferVariantPriceSerializer(_OfferPriceMixin, serializers.Serializer):
    id = serializers.IntegerField()
    price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    compare_at_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=0, required=False, allow_null=True
    )


class ProductOfferSerializer(_OfferPriceMixin, serializers.Serializer):
    """Prices for every size the offer changes. `price` / `compare_at_price`
    only matter for a product with no sizes."""

    variants = OfferVariantPriceSerializer(many=True, required=False, default=list)
    price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, required=False)
    compare_at_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=0, required=False, allow_null=True
    )


class AdminProductListSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name_ar", default="", read_only=True)
    brand_name = serializers.CharField(source="brand.name", default="", read_only=True)
    primary_image = serializers.SerializerMethodField()
    stock_state = serializers.SerializerMethodField()
    variant_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Product
        fields = [
            "id", "name_ar", "name_en", "slug", "sku", "status", "published", "featured",
            "price", "compare_at_price", "category", "category_name", "brand_name",
            "primary_image", "stock_state", "variant_count", "created_at",
        ]

    def get_primary_image(self, obj) -> str | None:
        images = list(obj.images.all())
        image = next((i for i in images if i.is_primary), None) or (images[0] if images else None)
        if not image or not image.image:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(image.image.url) if request else image.image.url

    def get_stock_state(self, obj) -> dict:
        """Aggregate across variants so the index can show a single badge."""
        available, low, tracked = 0, False, False
        for variant in obj.variants.all():
            item = getattr(variant, "inventory_item", None)
            if item is None:
                continue
            tracked = tracked or item.track_inventory
            available += item.available_quantity
            low = low or item.is_low_stock
        return {
            "available_quantity": available,
            "is_low_stock": low,
            "is_out_of_stock": tracked and available <= 0,
            "tracked": tracked,
        }


class AdminProductSerializer(serializers.ModelSerializer):
    images = AdminProductImageSerializer(many=True, read_only=True)
    variants = AdminProductVariantSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = [
            "id", "name_en", "name_ar", "slug", "sku", "barcode", "description", "short_description",
            "brand", "category", "product_type", "status", "published", "featured",
            "price", "compare_at_price", "cost_price", "tax_category",
            "weight_grams", "packaging_type", "badges", "food_metadata", "honey_metadata",
            "low_stock_threshold", "seo_title", "seo_description",
            "images", "variants", "created_at", "updated_at",
        ]


class AdminCategorySerializer(serializers.ModelSerializer):
    product_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Category
        fields = [
            "id", "name_en", "name_ar", "slug", "description", "parent", "image", "icon",
            "is_active", "sort_order", "seo_title", "seo_description", "product_count", "level",
        ]
        read_only_fields = ["level"]


# --------------------------------------------------------------------------
# Customers
# --------------------------------------------------------------------------
class AdminCustomerListSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    order_count = serializers.IntegerField(read_only=True, default=0)
    total_spent = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "email", "phone", "first_name", "last_name", "full_name", "is_active",
            "email_verified", "phone_verified", "order_count", "total_spent", "date_joined",
        ]

    def get_total_spent(self, obj) -> str:
        return str(getattr(obj, "total_spent", None) or 0)


class AdminCustomerDetailSerializer(AdminCustomerListSerializer):
    recent_orders = serializers.SerializerMethodField()
    profile = serializers.SerializerMethodField()

    class Meta(AdminCustomerListSerializer.Meta):
        fields = AdminCustomerListSerializer.Meta.fields + ["recent_orders", "profile", "last_login"]

    def get_recent_orders(self, obj) -> list:
        from django.db.models import Count

        orders = obj.orders.annotate(item_count=Count("items", distinct=True))[:10]
        return AdminOrderListSerializer(orders, many=True).data

    def get_profile(self, obj) -> dict | None:
        profile = getattr(obj, "profile", None)
        if profile is None:
            return None
        return {
            "date_of_birth": profile.date_of_birth,
            "gender": profile.gender,
            "language": profile.language,
            "marketing_consent": profile.marketing_consent,
        }


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------
class AdminInventoryItemSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="variant.sku", read_only=True)
    variant_name = serializers.SerializerMethodField()
    product_id = serializers.IntegerField(source="variant.product_id", read_only=True)
    product_name = serializers.CharField(source="variant.product.name_ar", read_only=True)
    quantity_on_hand = serializers.IntegerField(read_only=True)
    reserved_quantity = serializers.IntegerField(read_only=True)
    available_quantity = serializers.IntegerField(read_only=True)
    is_low_stock = serializers.BooleanField(read_only=True)
    is_out_of_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = InventoryItem
        fields = [
            "id", "variant", "product_id", "product_name", "sku", "variant_name", "track_inventory",
            "low_stock_threshold", "allow_backorder", "quantity_on_hand", "reserved_quantity",
            "available_quantity", "is_low_stock", "is_out_of_stock",
        ]

    def get_variant_name(self, obj) -> str:
        return str(obj.variant)


class AdminBatchSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="item.variant.sku", read_only=True)

    class Meta:
        model = Batch
        fields = [
            "id", "item", "sku", "batch_number", "production_date", "expiration_date",
            "initial_quantity", "quantity", "cost_price", "received_at", "is_active",
        ]


class AdminInventoryMovementSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="item.variant.sku", read_only=True)
    created_by_name = serializers.CharField(source="created_by.full_name", default="", read_only=True)

    class Meta:
        model = InventoryMovement
        fields = [
            "id", "item", "sku", "batch", "movement_type", "quantity_delta",
            "reference", "notes", "created_by", "created_by_name", "created_at",
        ]


class StockAdjustmentSerializer(serializers.Serializer):
    """A manual correction. Positive adds stock, negative removes it."""

    quantity_delta = serializers.IntegerField()
    reason = serializers.CharField(max_length=100)
    notes = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    batch_number = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    expiration_date = serializers.DateField(required=False, allow_null=True)

    def validate_quantity_delta(self, value):
        if value == 0:
            raise serializers.ValidationError("Adjustment cannot be zero.")
        return value


# --------------------------------------------------------------------------
# Marketing
# --------------------------------------------------------------------------
class AdminCouponSerializer(serializers.ModelSerializer):
    times_used = serializers.IntegerField(source="usage_count", read_only=True, default=0)

    class Meta:
        model = Coupon
        fields = [
            "id", "code", "description", "discount_type", "percentage_value", "fixed_amount_value",
            "minimum_order_amount", "maximum_discount_amount", "start_date", "expiration_date",
            "usage_limit", "per_user_limit", "products", "categories", "brands",
            "is_active", "times_used", "created_at",
        ]

    def validate(self, attrs):
        discount_type = attrs.get("discount_type", getattr(self.instance, "discount_type", None))
        percentage = attrs.get("percentage_value", getattr(self.instance, "percentage_value", None))
        fixed = attrs.get("fixed_amount_value", getattr(self.instance, "fixed_amount_value", None))
        if discount_type == "percentage" and percentage is None:
            raise serializers.ValidationError({"percentage_value": "Required for a percentage coupon."})
        if discount_type == "fixed_amount" and fixed is None:
            raise serializers.ValidationError({"fixed_amount_value": "Required for a fixed-amount coupon."})
        start, end = attrs.get("start_date"), attrs.get("expiration_date")
        if start and end and end <= start:
            raise serializers.ValidationError({"expiration_date": "Must be after the start date."})
        return attrs


# --------------------------------------------------------------------------
# Reviews
# --------------------------------------------------------------------------
class AdminReviewSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name_ar", read_only=True)
    customer_name = serializers.CharField(source="user.full_name", read_only=True)

    class Meta:
        model = Review
        fields = [
            "id", "product", "product_name", "user", "customer_name", "rating", "title",
            "comment", "verified_purchase", "approved", "helpful_votes", "created_at",
        ]
        read_only_fields = ["product", "user", "rating", "title", "comment", "verified_purchase", "helpful_votes"]


class AdminStoreReviewSerializer(serializers.ModelSerializer):
    """An opinion a branch customer gave, written down by staff.

    The name is what goes public, so it may not carry an email or a phone
    number, and nothing is published without the customer's consent."""

    product_name = serializers.CharField(source="product.name_ar", read_only=True, default="")
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default="")

    class Meta:
        model = StoreReview
        fields = [
            "id", "customer_name", "comment", "rating", "product", "product_name", "collected_on",
            "consent", "published", "created_by", "created_by_name", "created_at", "updated_at",
        ]
        read_only_fields = ["created_by", "created_at", "updated_at"]

    def validate_customer_name(self, value):
        value = " ".join(value.split())
        if not value:
            raise serializers.ValidationError("A name to show is required.")
        if "@" in value or re.search(r"\d{4,}", value):
            raise serializers.ValidationError("Use a first name and initial — never an email or a phone number.")
        return value

    def validate_comment(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("The customer's words are required.")
        return value

    def validate(self, attrs):
        consent = attrs.get("consent", getattr(self.instance, "consent", False))
        published = attrs.get("published", getattr(self.instance, "published", False))
        if published and not consent:
            raise serializers.ValidationError({"published": "Publish only with the customer's consent."})
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            validated_data["created_by"] = request.user
        return super().create(validated_data)


# --------------------------------------------------------------------------
# System
# --------------------------------------------------------------------------
class AdminAuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.full_name", default="", read_only=True)

    class Meta:
        model = AuditLog
        fields = [
            "id", "actor", "actor_name", "action", "model_name", "object_id",
            "changes", "ip_address", "path", "method", "created_at",
        ]


class AdminStaffSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id", "email", "first_name", "last_name", "full_name",
            "is_active", "is_staff", "is_superuser", "last_login", "date_joined",
        ]
        read_only_fields = ["is_superuser", "last_login", "date_joined"]


# --------------------------------------------------------------------------
# Shipping
# --------------------------------------------------------------------------
# Every money field on a rate is a fee or a bound on one; none can be negative.
_NON_NEGATIVE = {"min_value": 0}


class AdminShippingMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingMethod
        fields = [
            "id", "name", "method_type", "description", "carrier_code",
            "min_delivery_days", "max_delivery_days", "is_active", "sort_order",
        ]

    def validate(self, attrs):
        low = attrs.get("min_delivery_days", getattr(self.instance, "min_delivery_days", None))
        high = attrs.get("max_delivery_days", getattr(self.instance, "max_delivery_days", None))
        if low is not None and high is not None and high < low:
            raise serializers.ValidationError({"max_delivery_days": "Must not be less than the minimum."})
        return attrs


class AdminShippingRateSerializer(serializers.ModelSerializer):
    method_name = serializers.CharField(source="method.name", read_only=True)
    zone_name = serializers.CharField(source="zone.name", read_only=True)

    class Meta:
        model = ShippingRate
        fields = [
            "id", "zone", "zone_name", "method", "method_name", "calculation_type",
            "base_price", "price_per_kg", "min_weight_kg", "max_weight_kg",
            "min_order_amount", "free_shipping_threshold", "is_active",
        ]
        extra_kwargs = {
            "base_price": _NON_NEGATIVE,
            "price_per_kg": _NON_NEGATIVE,
            "min_order_amount": _NON_NEGATIVE,
            "free_shipping_threshold": _NON_NEGATIVE,
            "min_weight_kg": _NON_NEGATIVE,
            "max_weight_kg": _NON_NEGATIVE,
        }

    def validate(self, attrs):
        low = attrs.get("min_weight_kg", getattr(self.instance, "min_weight_kg", None))
        high = attrs.get("max_weight_kg", getattr(self.instance, "max_weight_kg", None))
        if low is not None and high is not None and high < low:
            raise serializers.ValidationError({"max_weight_kg": "Must not be less than the minimum weight."})
        return attrs


class AdminShippingZoneSerializer(serializers.ModelSerializer):
    rates = AdminShippingRateSerializer(many=True, read_only=True)
    governorates = serializers.PrimaryKeyRelatedField(many=True, queryset=Governorate.objects.all(), required=False)

    class Meta:
        model = ShippingZone
        fields = ["id", "name", "is_active", "governorates", "rates", "created_at"]

    def validate_governorates(self, value):
        # Quotes come from every zone a governorate is in, so a governorate in
        # two zones would be offered the same method twice at two prices.
        taken = Governorate.objects.filter(pk__in=[g.pk for g in value], zones__isnull=False)
        if self.instance is not None:
            taken = taken.exclude(zones=self.instance)
        names = sorted({g.name_en for g in taken})
        if names:
            raise serializers.ValidationError(f"Already in another zone: {', '.join(names)}.")
        return value


class AdminGovernorateShippingSerializer(serializers.ModelSerializer):
    """One row per governorate with what each method costs to deliver there.
    `zone.zone_size` tells the dashboard whether a fee change is shared with
    other governorates."""

    zone = serializers.SerializerMethodField()
    rates = serializers.SerializerMethodField()

    class Meta:
        model = Governorate
        fields = ["id", "code", "name_en", "name_ar", "is_active", "sort_order", "zone", "rates"]
        read_only_fields = ["code", "name_en", "name_ar", "sort_order"]

    def get_zone(self, obj) -> dict | None:
        zones = list(obj.zones.all())
        if not zones:
            return None
        zone = zones[0]
        return {
            "id": zone.id, "name": zone.name, "is_active": zone.is_active,
            "zone_size": len(zone.governorates.all()), "zone_count": len(zones),
        }

    def get_rates(self, obj) -> list:
        rates = [rate for zone in obj.zones.all() for rate in zone.rates.all()]
        return AdminShippingRateSerializer(rates, many=True).data


class GovernorateRateSerializer(serializers.Serializer):
    method = serializers.PrimaryKeyRelatedField(queryset=ShippingMethod.objects.all())
    base_price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    free_shipping_threshold = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=0, required=False, allow_null=True, default=None
    )
    is_active = serializers.BooleanField(required=False, default=True)
    apply_to_zone = serializers.BooleanField(required=False, default=False)


class AdminShipmentEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShipmentEvent
        fields = ["id", "status", "description", "location", "occurred_at"]


class AdminShipmentSerializer(serializers.ModelSerializer):
    shipping_method_name = serializers.CharField(source="shipping_method.name", read_only=True)
    events = AdminShipmentEventSerializer(many=True, read_only=True)

    class Meta:
        model = Shipment
        fields = [
            "id", "shipping_method", "shipping_method_name", "carrier_code", "tracking_number", "tracking_url",
            "status", "estimated_delivery", "shipped_at", "delivered_at", "events", "created_at", "updated_at",
        ]


#: Tracking numbers end up in a URL path (/shipping/track/<number>/), so a
#: slash or a space would make the customer's tracking link unreachable.
_TRACKING_NUMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ShipmentWriteSerializer(serializers.Serializer):
    carrier_code = serializers.CharField(max_length=32, required=False, allow_blank=True)
    tracking_number = serializers.CharField(max_length=100, required=False, allow_blank=True)
    tracking_url = serializers.URLField(max_length=200, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=ShipmentStatus.choices, required=False)
    estimated_delivery = serializers.DateField(required=False, allow_null=True)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True)

    def validate_tracking_number(self, value):
        if not value:
            return value
        if not _TRACKING_NUMBER.match(value):
            raise serializers.ValidationError("Use letters, digits, dots, dashes or underscores only.")
        # The customer's tracking route looks a shipment up by this number
        # alone, so two orders sharing one would make it ambiguous.
        others = Shipment.objects.filter(tracking_number=value)
        if self.context.get("order") is not None:
            others = others.exclude(order=self.context["order"])
        if others.exists():
            raise serializers.ValidationError("Another order already uses this tracking number.")
        return value

    def validate_tracking_url(self, value):
        # Rendered to the customer as a link, so only ever a web address.
        if value and not value.lower().startswith(("https://", "http://")):
            raise serializers.ValidationError("Must start with https:// or http://.")
        return value
