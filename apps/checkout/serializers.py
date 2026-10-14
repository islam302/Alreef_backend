from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers

from apps.orders.models import PaymentMethodSnapshot
from apps.orders.serializers import OrderDetailSerializer
from apps.shipping.models import City, Governorate
from apps.users.models import Address


class CheckoutInputSerializer(serializers.Serializer):
    address_id = serializers.IntegerField()
    shipping_method_id = serializers.IntegerField()
    payment_method = serializers.ChoiceField(choices=PaymentMethodSnapshot.choices)
    coupon_code = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    notes = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


# ---------------------------------------------------------------------------
# Guest checkout
# ---------------------------------------------------------------------------
# A guest has no address book, so the address travels inline and is validated
# against the same model (and the same length limits) an account's saved address
# is. It is never saved as an Address row: there is no user to own it, and the
# order keeps its own snapshot anyway.


class GuestContactSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=150)
    # Required: it is how the courier reaches them, how a COD order is confirmed
    # by phone, and the secret half of looking the order up later.
    phone = PhoneNumberField()
    # Optional: without one the guest simply gets no emails.
    email = serializers.EmailField(required=False, allow_blank=True, default="")

    def validate_full_name(self, value):
        value = value.strip()
        if len(value) < 2:
            raise serializers.ValidationError("Please enter your name.")
        return value


class GuestAddressSerializer(serializers.ModelSerializer):
    governorate = serializers.PrimaryKeyRelatedField(queryset=Governorate.objects.filter(is_active=True))
    city = serializers.PrimaryKeyRelatedField(
        queryset=City.objects.filter(is_active=True), required=False, allow_null=True, default=None,
    )

    class Meta:
        model = Address
        fields = [
            "governorate", "city", "district", "street", "building", "floor", "apartment",
            "landmark", "postal_code", "notes",
        ]

    def validate(self, attrs):
        city = attrs.get("city")
        if city is not None and city.governorate_id != attrs["governorate"].id:
            raise serializers.ValidationError({"city": "This city is not in the selected governorate."})
        return attrs


class GuestCheckoutInputSerializer(serializers.Serializer):
    contact = GuestContactSerializer()
    address = GuestAddressSerializer()
    shipping_method_id = serializers.IntegerField()
    payment_method = serializers.ChoiceField(choices=PaymentMethodSnapshot.choices)
    coupon_code = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    notes = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")

    def build_address(self) -> Address:
        """An unsaved Address: what checkout reads, with nobody to own it."""
        data = self.validated_data
        return Address(
            full_name=data["contact"]["full_name"], phone=data["contact"]["phone"], **data["address"],
        )


class GuestCheckoutSummarySerializer(serializers.Serializer):
    """
    The preview needs less than the order: the governorate prices shipping, and
    the phone — when the guest has typed it — lets the coupon's per-customer
    limit be checked before they press the button rather than after.
    """

    governorate = serializers.PrimaryKeyRelatedField(queryset=Governorate.objects.filter(is_active=True))
    phone = PhoneNumberField(required=False, allow_null=True, default=None)
    shipping_method_id = serializers.IntegerField()
    payment_method = serializers.ChoiceField(choices=PaymentMethodSnapshot.choices)
    coupon_code = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")

    def build_address(self) -> Address:
        data = self.validated_data
        return Address(governorate=data["governorate"], phone=data.get("phone") or "")


class CheckoutTotalsSerializer(serializers.Serializer):
    subtotal = serializers.SerializerMethodField()
    coupon_discount = serializers.SerializerMethodField()
    promotion_discount = serializers.SerializerMethodField()
    tax_total = serializers.SerializerMethodField()
    shipping_total = serializers.SerializerMethodField()
    cod_fee = serializers.SerializerMethodField()
    grand_total = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()
    free_shipping = serializers.BooleanField()

    def get_subtotal(self, obj):
        return str(obj.subtotal.amount)

    def get_coupon_discount(self, obj):
        return str(obj.coupon_discount.amount)

    def get_promotion_discount(self, obj):
        return str(obj.promotion_discount.amount)

    def get_tax_total(self, obj):
        return str(obj.tax_total.amount)

    def get_shipping_total(self, obj):
        return str(obj.shipping_total.amount)

    def get_cod_fee(self, obj):
        return str(obj.cod_fee.amount)

    def get_grand_total(self, obj):
        return str(obj.grand_total.amount)

    def get_currency(self, obj):
        return str(obj.grand_total.currency)


class CheckoutResultSerializer(serializers.Serializer):
    order = OrderDetailSerializer()
    payment = serializers.DictField()
    # Guest checkouts only: the one key that opens this order again without an
    # account. Shown once; the storefront keeps it for the tracking page.
    access_token = serializers.CharField(required=False)
