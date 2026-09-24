"""Manual/self-fulfilled shipping — no external carrier API call, staff sets
tracking manually via the admin. This is the default until a carrier
integration is configured with real credentials."""
from apps.shipping.providers.base import CreateShipmentResult, ShippingProvider, TrackingUpdate


class ManualShippingProvider(ShippingProvider):
    code = "manual"

    def create_shipment(self, *, shipment, order) -> CreateShipmentResult:
        return CreateShipmentResult(success=True, tracking_number="", tracking_url="", carrier_reference="")

    def cancel_shipment(self, *, shipment) -> bool:
        return True

    def get_tracking(self, *, shipment) -> list[TrackingUpdate]:
        return []
