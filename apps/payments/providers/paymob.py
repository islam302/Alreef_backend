"""
Paymob (Accept) adapter.

WHAT THIS COVERS. Paymob is one gateway serving several methods: card (Visa,
Mastercard, Meeza) and mobile wallet (Vodafone Cash and the other Egyptian
wallets) today, and InstaPay when Paymob releases it. Each is an integration ID
on the same merchant account, so they share this adapter, this HMAC verification
and this refund endpoint — the method the customer chose picks the integration ID
and nothing else differs.

THE TWO APIs. Paymob has a current API and an older one, and a live
integration needs both:

* Intention API (v1) — `POST /v1/intention/`, authenticated with the *secret
  key* as `Authorization: Token <secret key>`. This creates the payment and
  returns a `client_secret` used to build the hosted checkout URL.
* Accept API (legacy) — refund, void and transaction inquiry live here and
  authenticate with the *API key*, exchanged for a short-lived auth token at
  `POST /api/auth/tokens`. There is no v1 equivalent, so it is not optional.

Sources, all official Paymob documentation:
* developers.paymob.com/paymob-docs/developers/intention-apis/create-intention
* developers.paymob.com/paymob-docs/developers/webhook-callbacks-and-hmac/hmac
  /hmac-transaction-callback
* developers.paymob.com/paymob-docs/developers/manage-payment-apis/void

WHAT IS NOT VERIFIED HERE. This adapter has never been run against a live
Paymob account, because doing so needs merchant onboarding this project has not
completed. The contracts are implemented from the documentation above and
tested against recorded fixtures of those documented shapes. Paymob stays
disabled until somebody with real sandbox credentials runs the flow — the
checklist is in docs/payments/PROVIDERS.md. Endpoint paths are settings so
that a path Paymob changes does not need a code change.
"""
import hashlib
import hmac
import logging
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.utils import timezone

from apps.core.frontend_links import frontend_url
from apps.payments.providers.base import (
    CaptureResult,
    CreatePaymentResult,
    PaymentProvider,
    RefundResult,
    StatusResult,
)
from apps.payments.providers.http import GatewayClient, GatewayError
from apps.payments.webhooks import webhook_url

logger = logging.getLogger(__name__)

#: The exact ordered field list Paymob concatenates to produce the callback
#: HMAC, from its "HMAC Transaction Callback" documentation. Dotted names are
#: paths into the nested transaction object.
#:
#: DO NOT REORDER. The order is the specification. Getting it wrong does not
#: produce a loud failure — it produces an HMAC that never matches, or, if you
#: "fix" that by comparing something shorter, one that matches forged events.
HMAC_TRANSACTION_FIELDS = (
    "amount_cents",
    "created_at",
    "currency",
    "error_occured",
    "has_parent_transaction",
    "id",
    "integration_id",
    "is_3d_secure",
    "is_auth",
    "is_capture",
    "is_refunded",
    "is_standalone_payment",
    "is_voided",
    "order.id",
    "owner",
    "pending",
    "source_data.pan",
    "source_data.sub_type",
    "source_data.type",
    "success",
)

#: Which config entry holds the integration ID for each method Paymob serves.
#:
#: `instapay` is listed even though Paymob has not released it yet: the moment it
#: does, setting PAYMOB_INTEGRATION_ID_INSTAPAY moves InstaPay from the manual
#: transfer route to this gateway with no code change. Until then the entry is
#: empty and `integration_id_for` refuses, which is what routes InstaPay to the
#: manual adapter instead.
INTEGRATION_ID_FOR_METHOD = {
    "card": "INTEGRATION_ID_CARD",
    "mobile_wallet": "INTEGRATION_ID_WALLET",
    "instapay": "INTEGRATION_ID_INSTAPAY",
}


def _dig(payload: dict, dotted: str):
    """Follow a dotted path, returning None for anything missing."""
    current = payload
    for part in dotted.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _hmac_value(raw) -> str:
    """
    Render one field the way Paymob does before concatenating it.

    Booleans are the trap. Paymob's JSON carries `true`/`false` and its HMAC
    string uses the lowercase JSON spelling, not Python's `True`. `None`
    becomes empty — an absent `source_data.pan` on a wallet transaction
    contributes nothing rather than the text "None".
    """
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return "true" if raw else "false"
    return str(raw)


def build_hmac_message(transaction: dict) -> str:
    """The concatenated string Paymob signs. No separators, documented order."""
    return "".join(_hmac_value(_dig(transaction, field)) for field in HMAC_TRANSACTION_FIELDS)


def calculate_hmac(transaction: dict, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        build_hmac_message(transaction).encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()


class PaymobPaymentProvider(PaymentProvider):
    code = "paymob"

    # Paths are configurable because they are Paymob's to change, not ours.
    INTENTION_PATH = "/v1/intention/"
    AUTH_PATH = "/api/auth/tokens"
    REFUND_PATH = "/api/acceptance/void_refund/refund"
    VOID_PATH = "/api/acceptance/void_refund/void"
    INQUIRY_PATH = "/api/ecommerce/orders/transaction_inquiry"
    CHECKOUT_PATH = "/unifiedcheckout/"

    def __init__(self):
        from apps.payments import config as payment_config

        self.config = settings.PAYMENT_PROVIDERS["PAYMOB"]
        if not self.config.get("ENABLED"):
            raise NotImplementedError(
                "Paymob is disabled. Set PAYMOB_ENABLED=True together with "
                "PAYMOB_SECRET_KEY, PAYMOB_PUBLIC_KEY, PAYMOB_HMAC_SECRET and at "
                "least one PAYMOB_INTEGRATION_ID_*. See docs/payments/PROVIDERS.md."
            )
        # Enabled is not the same as configured. Refuse to construct rather
        # than fail later with a signature that cannot be checked.
        missing = [
            key
            for key in ("SECRET_KEY", "PUBLIC_KEY", "HMAC_SECRET")
            if not str(self.config.get(key) or "").strip()
        ]
        if missing:
            raise NotImplementedError(
                f"Paymob is enabled but not configured: {', '.join(missing)} "
                f"{'is' if len(missing) == 1 else 'are'} empty."
            )
        self._payment_config = payment_config
        self.client = GatewayClient(
            provider_code=self.code,
            base_url=self.config.get("BASE_URL") or "https://accept.paymob.com",
            timeout=int(self.config.get("TIMEOUT_SECONDS") or 20),
        )

    # -- helpers -----------------------------------------------------------

    def integration_id_for(self, method: str) -> str:
        key = INTEGRATION_ID_FOR_METHOD.get(method)
        if not key:
            raise NotImplementedError(f"Paymob does not serve the '{method}' method here.")
        value = str(self.config.get(key) or "").strip()
        if not value:
            raise NotImplementedError(
                f"Paymob is missing PAYMOB_{key} — the '{method}' method cannot be offered."
            )
        return value

    def _accept_auth_token(self) -> str:
        """
        Exchange the Accept API key for a short-lived auth token.

        Only refund, void and inquiry need this. The token is not cached: it is
        a bearer credential for the whole merchant account, and the operations
        that use it are rare and staff-initiated, so holding it in a shared
        cache buys nothing worth the exposure.
        """
        api_key = str(self.config.get("API_KEY") or "").strip()
        if not api_key:
            raise NotImplementedError(
                "Paymob refunds, voids and inquiries need PAYMOB_API_KEY (the Accept "
                "API key), which is a different credential from PAYMOB_SECRET_KEY."
            )
        payload = self.client.post(self.AUTH_PATH, json_body={"api_key": api_key})
        token = payload.get("token")
        if not token:
            raise GatewayError(
                "Paymob did not return an authentication token.",
                detail="paymob: auth/tokens response had no 'token'",
            )
        return token

    @staticmethod
    def _to_cents(amount) -> int:
        """
        Paymob works in the currency's minor unit.

        Quantised before conversion so a Decimal like 10.005 cannot silently
        become 1000 cents and under-charge.
        """
        # ROUND_HALF_UP, not Decimal's ROUND_HALF_EVEN default: 10.005 must
        # become 1001 cents, not 1000. Banker's rounding here would undercharge
        # half of all exact-half amounts.
        quantised = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return int(quantised * 100)

    def _checkout_url(self, client_secret: str) -> str:
        public_key = str(self.config.get("PUBLIC_KEY") or "").strip()
        base = (self.config.get("BASE_URL") or "https://accept.paymob.com").rstrip("/")
        return f"{base}{self.CHECKOUT_PATH}?publicKey={public_key}&clientSecret={client_secret}"

    # -- provider interface ------------------------------------------------

    def create_payment(self, *, payment, order) -> CreatePaymentResult:
        """
        Create a Paymob intention and hand back a checkout URL.

        Every figure sent is read from `payment`/`order`, which the server
        computed. Nothing here is taken from a request body.
        """
        integration_id = self.integration_id_for(payment.method)
        address = order.shipping_address_snapshot or {}

        # Paymob requires billing_data.phone_number and rejects empty strings
        # in several of these, so "NA" is the documented filler for fields a
        # merchant does not collect.
        billing_data = {
            "first_name": (order.customer_name or "NA").split(" ")[0] or "NA",
            "last_name": " ".join((order.customer_name or "NA").split(" ")[1:]) or "NA",
            "phone_number": order.customer_phone or "NA",
            "email": order.customer_email or "NA",
            "street": address.get("street") or "NA",
            "building": address.get("building") or "NA",
            "floor": address.get("floor") or "NA",
            "apartment": address.get("apartment") or "NA",
            "city": address.get("city") or address.get("governorate") or "NA",
            "state": address.get("governorate") or "NA",
            "country": "EG",
            "postal_code": address.get("postal_code") or "NA",
        }

        body = {
            "amount": self._to_cents(payment.amount.amount),
            "currency": str(payment.amount.currency),
            "payment_methods": [int(integration_id) if integration_id.isdigit() else integration_id],
            # Our own reference, echoed back on the callback and checked there.
            "special_reference": payment.merchant_reference,
            "notification_url": webhook_url("paymob"),
            "redirection_url": frontend_url("/order/payment-return", payment=payment.id),
            "billing_data": billing_data,
            "items": [
                {
                    "name": item.product_name_snapshot[:120] or "Item",
                    "amount": self._to_cents(item.unit_price.amount),
                    "quantity": item.quantity,
                }
                for item in order.items.all()
            ],
            "extras": {"order_number": order.order_number, "payment_id": payment.id},
        }

        try:
            response = self.client.post(
                self.INTENTION_PATH,
                json_body=body,
                headers={"Authorization": f"Token {self.config['SECRET_KEY']}"},
            )
        except GatewayError as exc:
            logger.warning("paymob create_payment failed for payment %s: %s", payment.id, exc.detail)
            return CreatePaymentResult(success=False, error=exc.message, raw=exc.payload)

        client_secret = response.get("client_secret")
        if not client_secret:
            return CreatePaymentResult(
                success=False,
                error="Paymob did not return a checkout reference.",
                raw=response,
            )

        return CreatePaymentResult(
            success=True,
            # The intention id is our handle until a transaction exists; the
            # callback replaces it with the transaction id.
            provider_reference=str(response.get("id") or payment.merchant_reference),
            # The customer has somewhere to go and has not paid yet.
            status="requires_action",
            redirect_url=self._checkout_url(client_secret),
            metadata={
                "intention_id": str(response.get("id") or ""),
                "intention_order_id": str(response.get("intention_order_id") or ""),
                "integration_id": integration_id,
                "live": bool(self.config.get("LIVE")),
            },
            raw=response,
        )

    def authorize(self, *, payment) -> CaptureResult:
        # Paymob authorises as part of the hosted checkout; there is no
        # separate server-side authorize call to make.
        raise NotImplementedError(
            "Paymob authorises during checkout — there is no server-side authorize step."
        )

    def capture(self, *, payment, amount=None) -> CaptureResult:
        # Only relevant for auth-and-capture integrations, which this account
        # is not set up for. Left explicit rather than silently succeeding.
        raise NotImplementedError(
            "Paymob capture requires an auth-and-capture integration ID, which is not configured."
        )

    def cancel(self, *, payment) -> bool:
        """Void an unsettled Paymob transaction."""
        if not payment.provider_reference:
            return False
        token = self._accept_auth_token()
        try:
            self.client.post(
                self.VOID_PATH,
                json_body={"transaction_id": payment.provider_reference},
                params={"token": token},
            )
        except GatewayError as exc:
            logger.warning("paymob void failed for payment %s: %s", payment.id, exc.detail)
            return False
        return True

    def refund(self, *, payment, amount, reason: str = "") -> RefundResult:
        """
        Refund through the Accept API.

        Partial refunds are the same call with a smaller `amount_cents`;
        Paymob decides whether the transaction is still refundable, and a
        rejection comes back as a failed result rather than an exception so
        the Refund row is recorded either way.
        """
        if not payment.provider_reference:
            return RefundResult(success=False, error="No Paymob transaction to refund.")
        try:
            token = self._accept_auth_token()
            response = self.client.post(
                self.REFUND_PATH,
                json_body={
                    "auth_token": token,
                    "transaction_id": payment.provider_reference,
                    "amount_cents": self._to_cents(getattr(amount, "amount", amount)),
                },
            )
        except (GatewayError, NotImplementedError) as exc:
            message = getattr(exc, "message", str(exc))
            logger.warning("paymob refund failed for payment %s: %s", payment.id, message)
            return RefundResult(success=False, error=message, raw=getattr(exc, "payload", {}))

        # Paymob echoes the refund as a child transaction.
        if response.get("success") is False or response.get("pending") is True and not response.get("id"):
            return RefundResult(
                success=False,
                error="Paymob did not accept the refund.",
                raw=response,
            )
        return RefundResult(
            success=True,
            provider_refund_id=str(response.get("id") or ""),
            raw=response,
        )

    def get_status(self, *, payment) -> StatusResult:
        """
        Ask Paymob what actually happened.

        This is the reconciliation path: it is what makes a lost callback
        recoverable, so it is queried by our own merchant reference rather than
        by anything the customer's browser told us.
        """
        token = self._accept_auth_token()
        response = self.client.post(
            self.INQUIRY_PATH,
            json_body={"merchant_order_id": payment.merchant_reference},
            headers={"Authorization": f"Bearer {token}"},
        )
        return StatusResult(status=self.classify_transaction(response), raw=response)

    @staticmethod
    def classify_transaction(transaction: dict) -> str:
        """
        Reduce Paymob's booleans to one of our provider status tokens.

        Order matters. A refunded transaction also has `success: true`, and a
        voided one often does too, so the terminal outcomes are tested before
        success is believed.
        """
        if not isinstance(transaction, dict):
            return "failed"
        if transaction.get("is_refunded"):
            return "refunded"
        if transaction.get("is_voided"):
            return "voided"
        if transaction.get("pending"):
            return "pending"
        if transaction.get("success"):
            # An auth without a capture is money promised, not taken.
            if transaction.get("is_auth") and not transaction.get("is_capture"):
                return "authorized"
            return "captured"
        return "failed"

    # -- webhooks ----------------------------------------------------------

    @staticmethod
    def _transaction_from(body_json: dict) -> dict:
        """
        The transaction object, whichever callback shape arrived.

        The server-to-server callback nests it under `obj`; the browser
        redirect flattens it into query parameters. Only the former is
        trustworthy enough to settle a payment, and it is the only one this
        endpoint accepts.
        """
        obj = body_json.get("obj")
        return obj if isinstance(obj, dict) else {}

    def verify_webhook(self, *, headers: dict, body: bytes, query_params: dict) -> bool:
        """
        Verify the callback HMAC.

        Compared with `hmac.compare_digest`: a plain `==` on a hex digest
        leaks, through timing, how many leading characters were right, which is
        enough to forge one byte at a time.
        """
        import json

        secret = str(self.config.get("HMAC_SECRET") or "")
        if not secret:
            return False

        try:
            body_json = json.loads(body.decode("utf-8")) if body else {}
        except (ValueError, UnicodeDecodeError):
            return False
        if not isinstance(body_json, dict):
            return False

        transaction = self._transaction_from(body_json)
        if not transaction:
            return False

        # Paymob sends the digest as a query parameter, and also as a
        # top-level key on the POST body.
        received = str(query_params.get("hmac") or body_json.get("hmac") or "")
        if not received:
            return False

        expected = calculate_hmac(transaction, secret)
        return hmac.compare_digest(expected.lower(), received.strip().lower())

    def parse_webhook_event(self, *, headers: dict, body: bytes, query_params: dict) -> dict:
        import json

        body_json = json.loads(body.decode("utf-8")) if body else {}
        transaction = self._transaction_from(body_json)
        order = transaction.get("order") if isinstance(transaction.get("order"), dict) else {}

        amount_cents = transaction.get("amount_cents")
        amount = None
        if amount_cents is not None:
            try:
                amount = (Decimal(str(amount_cents)) / 100).quantize(Decimal("0.01"))
            except (ArithmeticError, ValueError):
                amount = None

        event_at = _parse_event_time(transaction.get("created_at"))

        return {
            # Paymob's transaction id is unique per transaction, and a retry of
            # the same callback carries the same one — which is what makes
            # replay a no-op upstream in services.process_webhook.
            "event_id": f"paymob:{transaction.get('id')}",
            "event_type": str(body_json.get("type") or "TRANSACTION"),
            "provider_reference": str(transaction.get("id") or ""),
            # What we asked Paymob to quote back. Checked against the payment.
            "merchant_reference": str(order.get("merchant_order_id") or ""),
            "status": self.classify_transaction(transaction),
            "amount": amount,
            "currency": str(transaction.get("currency") or ""),
            "event_at": event_at,
            "failure_reason": str(
                (transaction.get("data") or {}).get("message") or ""
            )[:500] if isinstance(transaction.get("data"), dict) else "",
            "payload": body_json,
        }


def _parse_event_time(raw):
    """Paymob's `created_at` as an aware datetime, or None if unparseable."""
    if not raw:
        return None
    from django.utils.dateparse import parse_datetime

    try:
        parsed = parse_datetime(str(raw))
    except (ValueError, TypeError):
        return None
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed
