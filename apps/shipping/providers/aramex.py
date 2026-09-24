"""
Aramex adapter skeleton. Aramex requires a shipper account (ARAMEX_USERNAME,
ARAMEX_PASSWORD, ARAMEX_ACCOUNT_NUMBER) and their official SOAP/REST API
reference — this module defines the provider interface shape only. Wire the
real request/response mapping once you have Aramex's current official API
docs and account credentials; until then all methods raise
NotImplementedError so nothing pretends to work.
"""
from django.conf import settings

from apps.shipping.providers.base import CreateShipmentResult, ShippingProvider, TrackingUpdate


class AramexShippingProvider(ShippingProvider):
    code = "aramex"

    def __init__(self):
        self.config = settings.SHIPPING_PROVIDERS["ARAMEX"]
        if not self.config["ENABLED"]:
            raise NotImplementedError(
                "Aramex is not enabled. Set ARAMEX_ENABLED=True plus USERNAME/PASSWORD/ACCOUNT_NUMBER once "
                "you have verified Aramex's official API documentation and account credentials."
            )

    def create_shipment(self, *, shipment, order) -> CreateShipmentResult:
        raise NotImplementedError("Aramex create_shipment requires verified API integration with real credentials.")

    def cancel_shipment(self, *, shipment) -> bool:
        raise NotImplementedError("Aramex cancel_shipment requires verified API integration with real credentials.")

    def get_tracking(self, *, shipment) -> list[TrackingUpdate]:
        raise NotImplementedError("Aramex get_tracking requires verified API integration with real credentials.")
