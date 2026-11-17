"""Rounding money the way a customer is charged it."""
from decimal import ROUND_HALF_UP, Decimal

from djmoney.money import Money

CENT = Decimal("0.01")


def quantize_money(value: Money) -> Money:
    """
    Two decimals, half up.

    The columns hold two places, but a 14% VAT or a 15% discount carried four
    through the arithmetic: the checkout summary and the deposit message showed
    "372.5000" or "13.2125", and each column then rounded on its own, so the
    stored parts could miss the stored total by a piastre. Rounding each part
    before it is added keeps what is shown, stored and charged the same.
    """
    return Money(Decimal(value.amount).quantize(CENT, rounding=ROUND_HALF_UP), value.currency)
