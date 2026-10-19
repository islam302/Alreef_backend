"""
The settlement-route registry.

`get_payment_provider` takes the value in `Payment.provider`, not the method the
customer chose. Three routes:

* `paymob` — the one real gateway. Card and mobile wallet today; InstaPay too,
  once Paymob enables it.
* `instapay` — a bank transfer the customer makes and staff confirm. InstaPay
  publishes no merchant API, so there is nothing to call.
* `cod` — cash to the courier.

Use `apps.payments.config.gateway_for_method` to get from a method to a route
rather than guessing here.

Adapters are imported lazily. Constructing one reads settings and raises if the
route is disabled or half-configured, so importing them all at module load would
make `import apps.payments` fail on any box without Paymob credentials — which is
every development box and the whole test suite.
"""
from apps.payments.providers.base import PaymentProvider

__all__ = ["PaymentProvider", "get_payment_provider", "GATEWAY_CODES"]

#: Routes with a real adapter. `manage.py check_payments` reports on these.
GATEWAY_CODES = ("cod", "instapay", "paymob", "vodafone_cash")

#: Routes that exist as model choices but have no adapter, with the reason. Kept
#: explicit so the error names the actual situation instead of "unknown provider".
_NO_ADAPTER = {
    "manual": (
        "'manual' records a payment taken entirely outside the system (store credit, a "
        "bank transfer outside InstaPay) and has no adapter to call. Move its state with "
        "the staff endpoints instead."
    ),
}


def get_payment_provider(code: str) -> PaymentProvider:
    if code == "cod":
        from apps.payments.providers.cod import CODPaymentProvider

        return CODPaymentProvider()
    if code == "instapay":
        from apps.payments.providers.instapay import InstaPayPaymentProvider

        return InstaPayPaymentProvider()
    if code == "vodafone_cash":
        from apps.payments.providers.vodafone_cash import VodafoneCashPaymentProvider

        return VodafoneCashPaymentProvider()
    if code == "paymob":
        from apps.payments.providers.paymob import PaymobPaymentProvider

        return PaymobPaymentProvider()

    reason = _NO_ADAPTER.get(code)
    if reason:
        raise NotImplementedError(reason)
    raise NotImplementedError(f"Unknown payment route '{code}'.")
