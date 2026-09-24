"""
Which payment methods this installation can actually complete, and why not.

The storefront used to keep its own hard-coded list (`ENABLED_PAYMENT_METHODS`
in src/api/config.js). Two lists of the same fact drift, and the way they
drift is the bad way round: the shop offers a method the server cannot
complete, the customer picks it, and checkout 503s after they have entered an
address. This module is the single answer, and `GET /payments/methods/` is how
the storefront asks it.

THE RULE THAT MATTERS. A provider is not enabled because `PAYMOB_ENABLED=True`
is in the environment. It is enabled when the flag is set *and* every piece of
configuration its adapter will reach for is present. A half-configured gateway
that accepts a payment it cannot verify is worse than one that is switched off,
because the customer's money has moved and our record of it has not.

WHAT THIS SHOP ACCEPTS. Four methods, and no more: cash on delivery, card
(Visa / Mastercard / Meeza), mobile wallet (Vodafone Cash and the other Egyptian
wallets), and InstaPay. Fawry, valU, Sympl and Aman were implemented and then
removed at the business's request — an adapter for a method nobody sells is an
adapter nobody tests.

METHODS VS ROUTES. A method is what the customer picks; a route is what settles
it. Card and wallet are Paymob transactions. InstaPay has two possible routes and
picks the better one available: through Paymob if `PAYMOB_INTEGRATION_ID_INSTAPAY`
is set (Paymob lists InstaPay as coming soon), otherwise as a bank transfer the
customer makes and staff confirm — which is how Egyptian shops are paid by
InstaPay today, because InstaPay publishes no merchant API at all. See
docs/payments/instapay.md.
"""
from dataclasses import dataclass, field

from django.conf import settings

from apps.orders.models import PaymentMethodSnapshot


@dataclass(frozen=True)
class MethodSpec:
    """A payment method the storefront may offer."""

    method: str
    #: The gateway that holds the transaction — the value stored in
    #: `Payment.provider` and used to pick an adapter.
    gateway: str
    #: Key inside settings.PAYMENT_PROVIDERS.
    config_key: str
    #: Arabic label. The storefront shows these, so they live with the fact
    #: they describe rather than being duplicated in two frontends.
    label_ar: str
    label_en: str
    instructions_ar: str = ""
    #: Config entries that must be non-empty before this method is offered.
    requires: tuple = ()
    #: Whether the customer leaves our site to pay.
    redirects: bool = False
    #: Whether a refund can be issued through the provider's API.
    supports_refund: bool = True
    #: True when a refund is an act a person performs — handing cash back,
    #: transferring it back, issuing store credit — and then records here. Still a
    #: real refund with a real audit trail; just not one a gateway executes.
    refund_is_manual: bool = False
    #: How to describe that act for this specific method. "Give the cash back" is
    #: wrong advice for a bank transfer, and staff read this wording next to the
    #: button.
    manual_refund_note_ar: str = ""
    #: Extra config needed for refunds specifically. Absent means the method
    #: still works, but refunds must be done by hand in the provider's portal.
    refund_requires: tuple = ()
    sort_order: int = 0


#: Paymob credentials every Paymob-backed method needs.
#:
#: SECRET_KEY authenticates the Intention API. PUBLIC_KEY builds the checkout
#: URL. HMAC_SECRET verifies callbacks — without it we could take money and
#: have no trustworthy way to learn that we had, so it is mandatory rather
#: than optional.
_PAYMOB_BASE = ("SECRET_KEY", "PUBLIC_KEY", "HMAC_SECRET")

#: Paymob's refund, void and inquiry endpoints are on the older Accept API,
#: which authenticates with the API key rather than the secret key.
_PAYMOB_REFUND = ("API_KEY",)

METHOD_SPECS = (
    MethodSpec(
        method=PaymentMethodSnapshot.COD,
        gateway="cod",
        config_key="COD",
        label_ar="الدفع عند الاستلام",
        label_en="Cash on delivery",
        instructions_ar="تدفع قيمة الطلب كاش للمندوب وقت التسليم.",
        # Recordable, but no gateway executes it.
        refund_is_manual=True,
        manual_refund_note_ar=(
            "استرداد الدفع عند الاستلام بيتعمل بالإيد — سلّم الكاش للعميل أو اعمله رصيد "
            "في المحفظة، وبعدين سجّله هنا عشان الطلب يتظبط."
        ),
        sort_order=10,
    ),
    MethodSpec(
        method=PaymentMethodSnapshot.CARD,
        gateway="paymob",
        config_key="PAYMOB",
        label_ar="بطاقة بنكية",
        label_en="Card",
        instructions_ar="فيزا أو ماستركارد أو ميزة. هتكمّل الدفع على صفحة Paymob المؤمّنة.",
        requires=_PAYMOB_BASE + ("INTEGRATION_ID_CARD",),
        redirects=True,
        refund_requires=_PAYMOB_REFUND,
        sort_order=20,
    ),
    MethodSpec(
        method=PaymentMethodSnapshot.MOBILE_WALLET,
        gateway="paymob",
        config_key="PAYMOB",
        label_ar="محفظة موبايل — فودافون كاش",
        label_en="Mobile wallet",
        instructions_ar="فودافون كاش أو اتصالات أو أورنج أو وي. هتوصلك رسالة تأكيد على رقمك.",
        requires=_PAYMOB_BASE + ("INTEGRATION_ID_WALLET",),
        redirects=True,
        refund_requires=_PAYMOB_REFUND,
        sort_order=30,
    ),
    # --- InstaPay, the Paymob route ---------------------------------------
    #
    # Listed first of the two InstaPay entries, and `enabled_methods` keeps only
    # the first offerable spec per method code — so if Paymob ever serves
    # InstaPay, that route wins automatically and the manual one disappears from
    # checkout without anybody editing anything.
    MethodSpec(
        method=PaymentMethodSnapshot.INSTAPAY,
        gateway="paymob",
        config_key="PAYMOB",
        label_ar="إنستاباي",
        label_en="InstaPay",
        instructions_ar="هتكمّل التحويل على صفحة مؤمّنة من تطبيق البنك بتاعك.",
        requires=_PAYMOB_BASE + ("INTEGRATION_ID_INSTAPAY",),
        redirects=True,
        refund_requires=_PAYMOB_REFUND,
        sort_order=40,
    ),
    # --- InstaPay, the manual route ---------------------------------------
    #
    # The one that works today. The customer transfers from their own banking app
    # to the shop's InstaPay address and a staff member confirms it arrived.
    MethodSpec(
        method=PaymentMethodSnapshot.INSTAPAY,
        gateway="instapay",
        config_key="INSTAPAY",
        label_ar="إنستاباي — تحويل بنكي",
        label_en="InstaPay transfer",
        instructions_ar="هنعطيك عنوان إنستاباي بتاعنا، تحوّل من تطبيق بنكك، وبنأكد الطلب أول ما التحويل يوصل.",
        requires=("ADDRESS",),
        # No redirect: the customer leaves for their banking app, which is not
        # somewhere we can send them.
        redirects=False,
        refund_is_manual=True,
        manual_refund_note_ar=(
            "استرداد إنستاباي بيتعمل بالإيد — حوّل المبلغ تاني للعميل من تطبيق البنك، "
            "وبعدين سجّله هنا عشان الطلب يتظبط."
        ),
        sort_order=40,
    ),
)

def _preferred_spec_for(method: str):
    """
    The best route for `method` that is actually available, else the first spec.

    InstaPay is the reason this exists: there are two specs for it, the Paymob one
    and the manual one, and which applies depends on configuration. Availability
    decides, so enabling `PAYMOB_INTEGRATION_ID_INSTAPAY` switches the route with
    no code change — and with nothing left offering both at once.
    """
    candidates = [spec for spec in METHOD_SPECS if spec.method == method]
    if not candidates:
        return None
    for spec in candidates:
        if _spec_available(spec):
            return spec
    return candidates[0]


def _spec_available(spec) -> bool:
    """Whether one specific route's configuration is complete. No recursion."""
    return _spec_availability(spec).available


@dataclass
class MethodAvailability:
    spec: MethodSpec
    available: bool
    #: Config entries that are required and empty. Used by `manage.py
    #: check_payments` and surfaced to staff — never to customers, because it
    #: names our configuration.
    missing: list = field(default_factory=list)
    reason: str = ""


def provider_config(config_key: str) -> dict:
    return settings.PAYMENT_PROVIDERS.get(config_key, {})


def _missing_entries(config: dict, keys) -> list:
    return [key for key in keys if not str(config.get(key) or "").strip()]


def _spec_availability(spec) -> MethodAvailability:
    """Whether one specific route is usable, and if not, what is absent."""
    config = provider_config(spec.config_key)
    if not config:
        return MethodAvailability(
            spec=spec,
            available=False,
            reason=f"No PAYMENT_PROVIDERS['{spec.config_key}'] configuration block.",
        )
    if not config.get("ENABLED"):
        return MethodAvailability(
            spec=spec, available=False, reason=f"{spec.config_key}_ENABLED is not set."
        )

    missing = _missing_entries(config, spec.requires)
    if missing:
        return MethodAvailability(
            spec=spec,
            available=False,
            missing=missing,
            reason=(
                f"{spec.config_key} is enabled but these settings are empty: "
                f"{', '.join(missing)}."
            ),
        )
    return MethodAvailability(spec=spec, available=True)


def availability(method: str) -> MethodAvailability:
    """
    Whether `method` can be offered, and if not, precisely what is absent.

    A method may have more than one route — InstaPay has the Paymob one and the
    manual-transfer one — and it is offerable if *either* works. When neither
    does, the reason names every route, because an operator asking "why is
    InstaPay off?" needs to know both answers to pick which one to fix. Reporting
    only the first route's reason sent them to configure Paymob when setting
    `INSTAPAY_ADDRESS` was all it needed.

    Deliberately does not import any adapter: this must be answerable while a
    gateway is switched off, and importing an adapter raises in that case.
    """
    candidates = [spec for spec in METHOD_SPECS if spec.method == method]
    if not candidates:
        return MethodAvailability(
            spec=None, available=False, reason=f"Unknown payment method '{method}'."
        )

    results = [_spec_availability(spec) for spec in candidates]
    for result in results:
        if result.available:
            return result

    if len(results) == 1:
        return results[0]

    # Several routes, none usable. Keep the first as the nominal spec (it is the
    # preferred route) but carry every reason.
    combined = " ".join(
        f"[{result.spec.gateway}] {result.reason}" for result in results
    )
    missing = [entry for result in results for entry in result.missing]
    return MethodAvailability(
        spec=results[0].spec, available=False, missing=missing, reason=combined
    )


def is_available(method: str) -> bool:
    return availability(method).available


def refund_available(method: str) -> tuple:
    """
    `(can_refund, reason)` for `method`.

    A method can be live for taking money and still not support refunds
    through the API — Paymob's refund endpoint needs the Accept API key, and
    COD refunds are physically cash or store credit. Staff see the reason in
    the dashboard rather than a refund button that always fails.
    """
    spec = _preferred_spec_for(method)
    if spec is None:
        return False, f"Unknown payment method '{method}'."
    if not spec.supports_refund:
        return False, f"{spec.label_en} refunds are not supported."
    if spec.refund_is_manual:
        # Permitted, and the caller is told it is on them to move the money — in
        # the words that fit this method, because "hand the cash back" is wrong
        # advice for a bank transfer.
        return True, (
            spec.manual_refund_note_ar
            or f"{spec.label_en} refunds are settled by hand, then recorded here."
        )
    missing = _missing_entries(provider_config(spec.config_key), spec.refund_requires)
    if missing:
        return False, (
            f"{spec.config_key} refunds need these settings: {', '.join(missing)}."
        )
    return True, ""


def gateway_for_method(method: str) -> str:
    """
    The `Payment.provider` value for `method`.

    Raises `ApplicationError` rather than returning a default: picking a
    gateway by guesswork is how a payment ends up recorded against the wrong
    provider and then reconciled against the wrong account.
    """
    from apps.core.error_codes import ApplicationError, ErrorCodes

    spec = _preferred_spec_for(method)
    if spec is None:
        raise ApplicationError(
            ErrorCodes.PAYMENT_METHOD_NOT_AVAILABLE,
            f"Payment method '{method}' is not supported.",
        )
    return spec.gateway


def enabled_methods() -> list:
    """
    Every offerable method, in display order, each appearing once.

    The de-duplication matters: InstaPay has two specs (Paymob and manual), and a
    checkout page showing "إنستاباي" twice would be a bug the customer sees.
    `_preferred_spec_for` decides which one, so whichever route is configured is
    the one offered.
    """
    offered = []
    for method in method_codes():
        state = availability(method)
        if state.available:
            offered.append(state.spec)
    return sorted(offered, key=lambda spec: spec.sort_order)


def method_codes() -> list:
    """Every method code this shop knows about, once each, in declaration order."""
    seen = []
    for spec in METHOD_SPECS:
        if spec.method not in seen:
            seen.append(spec.method)
    return seen


def enabled_method_codes() -> list:
    return [spec.method for spec in enabled_methods()]


def cod_limits():
    """
    `(fee, minimum, maximum)` for cash on delivery, as Decimals.

    Read at call time, not at import, so changing the environment does not
    need a code deploy and so tests can override settings.
    """
    from decimal import Decimal

    config = provider_config("COD")
    return (
        Decimal(str(config.get("FEE_AMOUNT", "0") or "0")),
        Decimal(str(config.get("MIN_ORDER_AMOUNT", "0") or "0")),
        Decimal(str(config.get("MAX_ORDER_AMOUNT", "0") or "0")),
    )


def cod_allowed_shipping_method_ids():
    """
    Shipping methods COD may be combined with, or `None` for "any".

    A courier who does not handle cash cannot take a COD order, and that is a
    per-carrier fact rather than a global one.
    """
    raw = provider_config("COD").get("ALLOWED_SHIPPING_METHOD_IDS") or ""
    ids = [part.strip() for part in str(raw).split(",") if part.strip()]
    if not ids:
        return None
    return {int(value) for value in ids if value.isdigit()}
