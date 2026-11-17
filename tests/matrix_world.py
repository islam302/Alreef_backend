"""
A small, realistic shop for the matrix and property suites.

Three governorates in two shipping zones, two shipping methods with their own
fees and free-shipping thresholds, a VAT-able and a zero-rated product, and
every payment route the shop sells with fixture configuration. Plus the
helpers that drive a customer — guest, signed-in with a saved address, or
signed-in adding one at checkout — through the real HTTP endpoints, and an
independent oracle for what an order must cost.

Not a test module itself (no `test_` prefix): tests/test_matrix_*.py and
tests/test_properties_money.py import it. Every function here takes or builds
DB rows, so call it from a `db`-marked test.
"""
import datetime
import math
import uuid
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from django.utils import timezone
from djmoney.money import Money
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.brands.models import Brand
from apps.catalog.models import Product, ProductStatus, ProductType, ProductVariant, TaxCategory
from apps.categories.models import Category
from apps.coupons.models import Coupon, CouponDiscountType
from apps.inventory.models import Batch
from apps.inventory.services import get_or_create_item
from apps.promotions.models import Promotion, PromotionType
from apps.shipping.models import Governorate, RateCalculationType, ShippingMethod, ShippingRate, ShippingZone
from apps.users.models import Address, User

API = "/api/v1"
CENT = Decimal("0.01")
VAT = Decimal("0.14")
COD_FEE = Decimal("15.00")
COD_MAX = Decimal("50000.00")
DEPOSIT = Decimal("100.00")
GUEST_PHONE = "01001234567"
GUEST_PHONE_E164 = "+201001234567"

INSTAPAY = {
    "ENABLED": True,
    "ADDRESS": "alreef@instapay",
    "ACCOUNT_NAME": "شركة الريف",
    "PAY_LINK": "https://ipn.eg/S/alreef/instapay/0aBcD1",
    "CONFIRM_WINDOW_HOURS": 24,
}
WALLET = {"ENABLED": True, "NUMBER": "010 1234 5678", "ACCOUNT_NAME": "شركة الريف", "CONFIRM_WINDOW_HOURS": 24}
WALLET_NUMBER = "01012345678"


def q(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def capped_discounts(subtotal, coupon_discount, promotion_discount):
    """Discounts never exceed the subtotal: promotions first, the coupon takes what is left."""
    promotion_discount = min(promotion_discount, subtotal)
    coupon_discount = min(coupon_discount, subtotal - promotion_discount)
    return coupon_discount, promotion_discount


def vat_after_discount(lines, discount) -> Decimal:
    """
    VAT on what the customer pays for each line: a whole-cart discount is
    shared over the lines in proportion to their totals, rounded to piastres
    cumulatively (the remainder lands on the last line), and 14% is charged on
    each taxable line's total less its share.
    """
    totals = [q(Decimal(price) * qty) for price, qty, _ in lines]
    subtotal = sum(totals, Decimal("0"))
    d_cents = Fraction(int(q(discount) * 100))
    s_cents = Fraction(int(subtotal * 100))
    tax, running, before = Decimal("0"), Fraction(0), 0
    for (price, qty, taxable), line_total in zip(lines, totals):
        running += d_cents * Fraction(int(line_total * 100)) / s_cents if s_cents else 0
        rounded = math.floor(running + Fraction(1, 2))
        share = Decimal(rounded - before) / 100
        before = rounded
        if taxable:
            tax += q((line_total - share) * VAT)
    return tax


# ---------------------------------------------------------------------------
# Payment configuration
# ---------------------------------------------------------------------------

#: route name -> (checkout payment_method, deposit_route, COD deposit amount)
PAYMENT_ROUTES = {
    "cod": ("cod", "", Decimal("0")),
    "cod_deposit_instapay": ("cod", "instapay", DEPOSIT),
    "cod_deposit_vodafone": ("cod", "vodafone_cash", DEPOSIT),
    "instapay": ("instapay", "", Decimal("0")),
    "vodafone_cash": ("mobile_wallet", "", Decimal("0")),
}


def payment_providers(*, deposit=Decimal("0"), cod_max=COD_MAX, cod_min=Decimal("0"), fee=COD_FEE, **overrides):
    """Every block spelled out, so nothing leaks in from the developer's .env."""
    providers = {
        "PAYMOB": {"ENABLED": False},
        "INSTAPAY": dict(INSTAPAY),
        "VODAFONE_CASH": dict(WALLET),
        "COD": {
            "ENABLED": True,
            "FEE_AMOUNT": str(fee),
            "MIN_ORDER_AMOUNT": str(cod_min),
            "MAX_ORDER_AMOUNT": str(cod_max),
            "ALLOWED_SHIPPING_METHOD_IDS": "",
            "DEPOSIT_AMOUNT": str(deposit),
        },
    }
    providers.update(overrides)
    return providers


def configure(settings, route="cod", **kwargs):
    method, deposit_route, deposit = PAYMENT_ROUTES[route]
    kwargs.setdefault("deposit", deposit)
    settings.PAYMENT_PROVIDERS = payment_providers(**kwargs)
    settings.FRONTEND_URL = "https://shop.example.test"
    settings.BACKEND_PUBLIC_URL = "https://api.example.test"
    return method, deposit_route


# ---------------------------------------------------------------------------
# The shop
# ---------------------------------------------------------------------------

#: (zone, method) -> (fee, free-shipping threshold or None)
SHIPPING_TABLE = {
    ("metro", "standard"): (Decimal("30.00"), Decimal("500.00")),
    ("metro", "express"): (Decimal("60.00"), Decimal("1000.00")),
    ("rest", "standard"): (Decimal("50.00"), Decimal("750.00")),
    ("rest", "express"): (Decimal("90.00"), None),  # never free
}
GOVERNORATE_ZONE = {"cairo": "metro", "giza": "metro", "aswan": "rest"}


@dataclass
class World:
    governorates: dict
    methods: dict
    taxed: ProductVariant
    zero_rated: ProductVariant
    category: Category
    brand: Brand
    extra: dict = field(default_factory=dict)

    def zone_of(self, governorate_code):
        return GOVERNORATE_ZONE[governorate_code]

    def shipping_rule(self, governorate_code, method_code):
        return SHIPPING_TABLE[(self.zone_of(governorate_code), method_code)]


def make_variant(category, brand, *, sku, price, tax=TaxCategory.STANDARD, stock=500, product_status=ProductStatus.ACTIVE,
                 published=True, variant_active=True, batches=None):
    product = Product.objects.create(
        name_en=f"Honey {sku}", name_ar=f"عسل {sku}", slug=f"honey-{sku.lower()}", sku=f"P-{sku}",
        category=category, brand=brand, product_type=ProductType.SIMPLE, status=product_status,
        published=published, price=Decimal(price), tax_category=tax,
    )
    variant = ProductVariant.objects.create(
        product=product, sku=f"V-{sku}", name_suffix="500g", price=Decimal(price), is_active=variant_active, is_default=True,
    )
    item = get_or_create_item(variant)
    for number, (quantity, days) in enumerate(batches or [(stock, 365)]):
        Batch.objects.create(
            item=item, batch_number=f"B-{sku}-{number}", quantity=quantity, initial_quantity=quantity,
            expiration_date=timezone.localdate() + datetime.timedelta(days=days), is_active=True,
        )
    return variant


def build_world(stock=500) -> World:
    governorates = {
        "cairo": Governorate.objects.create(code="cairo", name_en="Cairo", name_ar="القاهرة", is_active=True),
        "giza": Governorate.objects.create(code="giza", name_en="Giza", name_ar="الجيزة", is_active=True),
        "aswan": Governorate.objects.create(code="aswan", name_en="Aswan", name_ar="أسوان", is_active=True),
    }
    zones = {
        "metro": ShippingZone.objects.create(name="Greater Cairo", is_active=True),
        "rest": ShippingZone.objects.create(name="Rest of Egypt", is_active=True),
    }
    zones["metro"].governorates.add(governorates["cairo"], governorates["giza"])
    zones["rest"].governorates.add(governorates["aswan"])
    methods = {
        "standard": ShippingMethod.objects.create(name="Standard", method_type="standard", is_active=True, sort_order=1),
        "express": ShippingMethod.objects.create(name="Express", method_type="express", is_active=True, sort_order=2),
    }
    for (zone, method), (fee, threshold) in SHIPPING_TABLE.items():
        ShippingRate.objects.create(
            zone=zones[zone], method=methods[method], calculation_type=RateCalculationType.FLAT,
            base_price=fee, free_shipping_threshold=threshold, is_active=True,
        )
    category = Category.objects.create(name_en="Honey", name_ar="عسل", slug="honey", is_active=True)
    brand = Brand.objects.create(name="Alreef", slug="alreef", is_active=True)
    taxed = make_variant(category, brand, sku="TAXED", price="100.00", stock=stock)
    zero_rated = make_variant(category, brand, sku="ZERO", price="100.00", tax=TaxCategory.ZERO_RATED, stock=stock)
    return World(governorates=governorates, methods=methods, taxed=taxed, zero_rated=zero_rated, category=category, brand=brand)


# ---------------------------------------------------------------------------
# Discounts
# ---------------------------------------------------------------------------

DISCOUNTS = ("none", "coupon_pct", "coupon_fixed", "promotion", "coupon_and_promotion")
COUPON_PCT = Decimal("10")
COUPON_FIXED = Decimal("75.00")
PROMO_PCT = Decimal("5")


def make_coupon(code="SAVE", *, kind=CouponDiscountType.PERCENTAGE, value=COUPON_PCT, **fields):
    data = {"code": code, "discount_type": kind, "is_active": True, **fields}
    if kind == CouponDiscountType.PERCENTAGE:
        data["percentage_value"] = Decimal(value)
    elif kind == CouponDiscountType.FIXED_AMOUNT:
        data["fixed_amount_value"] = Money(Decimal(value), "EGP")
    return Coupon.objects.create(**data)


def make_promotion(*, pct=PROMO_PCT, kind=PromotionType.PERCENTAGE, fixed=None, stackable=False, priority=0, name="Promo"):
    data = {"name": name, "promotion_type": kind, "is_active": True, "stackable": stackable, "priority": priority}
    if kind == PromotionType.PERCENTAGE:
        data["percentage_value"] = Decimal(pct)
    elif kind == PromotionType.FIXED_AMOUNT:
        data["fixed_amount_value"] = Money(Decimal(fixed), "EGP")
    return Promotion.objects.create(**data)


def apply_discount(discount):
    """Create what the discount case needs; return the coupon code to send."""
    code = ""
    if discount in ("coupon_pct", "coupon_and_promotion"):
        make_coupon("PCT10", kind=CouponDiscountType.PERCENTAGE, value=COUPON_PCT)
        code = "pct10"  # codes are matched case-insensitively; send it as a customer types it
    elif discount == "coupon_fixed":
        make_coupon("FIX75", kind=CouponDiscountType.FIXED_AMOUNT, value=COUPON_FIXED)
        code = "FIX75"
    if discount in ("promotion", "coupon_and_promotion"):
        make_promotion()
    return code


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

CUSTOMERS = ("guest", "saved_address", "new_address")


def jwt_client(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


@dataclass
class Shopper:
    kind: str
    client: APIClient
    governorate: Governorate
    user: User | None = None
    address_id: int | None = None

    def add(self, variant, quantity):
        response = self.client.post(f"{API}/cart/items/", {"variant_id": variant.id, "quantity": quantity}, format="json")
        assert response.status_code == 200, response.content
        return response

    def summary_body(self, *, shipping_method_id, payment_method, coupon_code=""):
        body = {"shipping_method_id": shipping_method_id, "payment_method": payment_method, "coupon_code": coupon_code}
        if self.user is None:
            body.update({"governorate": self.governorate.id, "phone": GUEST_PHONE})
        else:
            body["address_id"] = self.address_id
        return body

    def checkout_body(self, *, shipping_method_id, payment_method, coupon_code="", deposit_route="", **extra):
        body = {
            "shipping_method_id": shipping_method_id, "payment_method": payment_method,
            "coupon_code": coupon_code, "deposit_route": deposit_route, **extra,
        }
        if self.user is None:
            body["contact"] = {"full_name": "Guest Buyer", "phone": GUEST_PHONE, "email": "guest@example.com"}
            body["address"] = {
                "governorate": self.governorate.id, "district": "Downtown", "street": "12 Tahrir St", "building": "3",
            }
        else:
            body["address_id"] = self.address_id
        return body

    def summary(self, **kwargs):
        return self.client.post(f"{API}/checkout/summary/", self.summary_body(**kwargs), format="json")

    def checkout(self, *, key=None, body=None, **kwargs):
        return self.client.post(
            f"{API}/checkout/", body if body is not None else self.checkout_body(**kwargs), format="json",
            HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()),
        )


_user_seq = iter(range(1, 10**9))


def make_shopper(kind, governorate, *, email=None, phone=None) -> Shopper:
    if kind == "guest":
        return Shopper(kind=kind, client=APIClient(), governorate=governorate)
    n = next(_user_seq)
    user = User.objects.create_user(
        email=email or f"buyer{n}-{uuid.uuid4().hex[:6]}@example.com", password="SuperSecret123",
        first_name="Buyer", last_name="Matrix", phone=phone,
    )
    client = jwt_client(user)
    if kind == "saved_address":
        address = Address.objects.create(
            user=user, full_name="Buyer Matrix", phone="+201001112223", governorate=governorate,
            district="Maadi", street="9 Road", building="7", is_default=True,
        )
        address_id = address.id
    elif kind == "new_address":
        assert not Address.objects.filter(user=user).exists()
        response = client.post(
            f"{API}/addresses/",
            {"full_name": "Buyer Matrix", "phone": "01001112223", "governorate": governorate.id,
             "district": "Maadi", "street": "9 Road", "building": "7"},
            format="json",
        )
        assert response.status_code == 201, response.content
        address_id = response.json()["data"]["id"]
    else:
        raise ValueError(kind)
    return Shopper(kind=kind, client=client, governorate=governorate, user=user, address_id=address_id)


# ---------------------------------------------------------------------------
# The oracle
# ---------------------------------------------------------------------------


@dataclass
class Expected:
    subtotal: Decimal
    coupon_discount: Decimal
    promotion_discount: Decimal
    tax_total: Decimal
    shipping_total: Decimal
    cod_fee: Decimal
    grand_total: Decimal
    free_shipping: bool
    deposit: Decimal | None
    due_on_delivery: Decimal | None


def expected_totals(*, lines, shipping_fee, threshold, payment_method, discount="none", deposit=Decimal("0"),
                    coupon=None, promotions=()):
    """
    What the order must cost, computed from first principles.

    `lines`: [(unit_price, quantity, taxable)]. `coupon`: (kind, value) or None.
    `promotions`: [(kind, value)] applied to the whole cart, all of them (the
    caller passes only what should stack). The discounts are capped at the
    subtotal, VAT is 14% of what is paid for each taxable line after its share
    of the discounts, and the free-shipping threshold is compared with the
    undiscounted subtotal.
    """
    subtotal = sum((q(Decimal(price) * qty) for price, qty, _ in lines), Decimal("0.00"))
    if coupon is None and discount in ("coupon_pct", "coupon_and_promotion"):
        coupon = ("percentage", COUPON_PCT)
    elif coupon is None and discount == "coupon_fixed":
        coupon = ("fixed_amount", COUPON_FIXED)
    if not promotions and discount in ("promotion", "coupon_and_promotion"):
        promotions = [("percentage", PROMO_PCT)]

    coupon_discount = Decimal("0.00")
    if coupon:
        kind, value = coupon
        coupon_discount = q(subtotal * Decimal(value) / 100) if kind == "percentage" else q(min(Decimal(value), subtotal))
    promotion_discount = Decimal("0.00")
    for kind, value in promotions:
        promotion_discount += q(subtotal * Decimal(value) / 100) if kind == "percentage" else q(min(Decimal(value), subtotal))

    coupon_discount, promotion_discount = capped_discounts(subtotal, coupon_discount, promotion_discount)
    discounted = subtotal - coupon_discount - promotion_discount
    tax = vat_after_discount(lines, coupon_discount + promotion_discount)
    free = bool(threshold is not None and subtotal >= threshold)
    shipping = Decimal("0.00") if free else q(shipping_fee)
    fee = COD_FEE if payment_method == "cod" else Decimal("0.00")
    grand = q(discounted + tax + shipping + fee)
    dep = due = None
    if payment_method == "cod" and deposit > 0:
        dep = q(min(deposit, grand))
        due = q(grand - dep)
    return Expected(subtotal, coupon_discount, promotion_discount, tax, shipping, fee, grand, False, dep, due)


def money(value) -> Decimal:
    """
    A response's money string as a Decimal, which must be a whole number of
    piastres ("12.5000" or "0.125" fail). The string's own shape is checked
    separately (test_matrix_checkout: the summary prints every amount with two decimals).
    """
    amount = Decimal(str(value))
    assert amount == amount.quantize(CENT), f"not a whole number of piastres: {value!r}"
    assert amount.as_tuple().exponent >= -2, f"more than two decimals: {value!r}"
    return amount
