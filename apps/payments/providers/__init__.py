from .base import PaymentProvider
from .cod import CODPaymentProvider

__all__ = ["PaymentProvider", "get_payment_provider"]

_REGISTRY = {
    "cod": CODPaymentProvider,
}


def get_payment_provider(code: str) -> PaymentProvider:
    if code in _REGISTRY:
        return _REGISTRY[code]()

    if code == "paymob":
        from .paymob import PaymobPaymentProvider

        return PaymobPaymentProvider()
    if code == "fawry":
        from .fawry import FawryPaymentProvider

        return FawryPaymentProvider()
    if code == "valu":
        from .valu import ValuPaymentProvider

        return ValuPaymentProvider()
    if code == "sympl":
        from .sympl import SymplPaymentProvider

        return SymplPaymentProvider()
    if code == "aman":
        from .aman import AmanPaymentProvider

        return AmanPaymentProvider()

    raise NotImplementedError(f"Unknown or unconfigured payment provider '{code}'.")
