"""
Shipping provider abstraction (spec §46). New carriers (Aramex, Bosta,
Mylerz, ShipBlu, DHL, ...) plug in by implementing ShippingProvider without
touching Order/Shipment logic. Only implement a concrete adapter once you
have verified official API docs and sandbox credentials for that carrier.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class CreateShipmentResult:
    success: bool
    tracking_number: str = ""
    tracking_url: str = ""
    carrier_reference: str = ""
    raw: Any = None
    error: str = ""


@dataclass
class TrackingUpdate:
    status: str
    description: str
    location: str
    occurred_at: str
    raw: Any = None


class ShippingProvider(ABC):
    code: str = "base"

    @abstractmethod
    def create_shipment(self, *, shipment, order) -> CreateShipmentResult:
        raise NotImplementedError

    @abstractmethod
    def cancel_shipment(self, *, shipment) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_tracking(self, *, shipment) -> list[TrackingUpdate]:
        raise NotImplementedError

    def verify_webhook(self, *, headers: dict, body: bytes) -> bool:
        """Override for carriers that sign webhook payloads. Fails closed by
        default — a concrete adapter must explicitly prove it verified a
        signature before any webhook is trusted; no adapter should inherit
        an "accept everything" default."""
        return False
