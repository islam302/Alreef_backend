from .base import ShippingProvider
from .manual import ManualShippingProvider

__all__ = ["ShippingProvider", "get_shipping_provider"]


def get_shipping_provider(carrier_code: str = "manual") -> ShippingProvider:
    if carrier_code in ("", "manual", "self"):
        return ManualShippingProvider()

    if carrier_code == "bosta":
        from .bosta import BostaShippingProvider

        return BostaShippingProvider()

    if carrier_code == "aramex":
        from .aramex import AramexShippingProvider

        return AramexShippingProvider()

    raise NotImplementedError(f"Unknown or unconfigured shipping provider '{carrier_code}'.")
