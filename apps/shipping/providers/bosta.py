"""
Bosta adapter skeleton. Bosta requires a merchant account and API key
(BOSTA_API_KEY) obtained from https://bosta.co — this adapter implements the
provider interface but does NOT fabricate undocumented endpoints. Wire the
real request/response mapping once you have Bosta's official API reference
and sandbox credentials; until SHIPPING_PROVIDERS['BOSTA']['ENABLED'] is
True and credentials are present, requests raise NotImplementedError.
"""
from django.conf import settings

from apps.shipping.providers.base import CreateShipmentResult, ShippingProvider, TrackingUpdate


class BostaShippingProvider(ShippingProvider):
    code = "bosta"

    def __init__(self):
        self.config = settings.SHIPPING_PROVIDERS["BOSTA"]
        if not self.config["ENABLED"]:
            raise NotImplementedError(
                "Bosta is not enabled. Set BOSTA_ENABLED=True and BOSTA_API_KEY once you have "
                "verified Bosta's official API documentation and sandbox credentials."
            )

    def create_shipment(self, *, shipment, order) -> CreateShipmentResult:
        raise NotImplementedError("Bosta create_shipment requires verified API integration with real credentials.")

    def cancel_shipment(self, *, shipment) -> bool:
        raise NotImplementedError("Bosta cancel_shipment requires verified API integration with real credentials.")

    def get_tracking(self, *, shipment) -> list[TrackingUpdate]:
        raise NotImplementedError("Bosta get_tracking requires verified API integration with real credentials.")
