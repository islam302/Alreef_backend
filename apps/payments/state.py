"""
The payment state machine.

Before this module existed, `mark_payment_failed` would happily move a
CAPTURED payment to FAILED, and `PaymentStatusView` assigned whatever string a
provider's status endpoint returned straight onto `payment.status`. Both are
the same bug: money state changed without anybody asking whether the change
was legal. A provider that replays an old event, a reconciliation job that
runs while a webhook is in flight, or a staff member clicking twice could all
walk a paid order backwards.

So every status change in this app goes through `assert_transition`, and the
table below is the only place the rules live.

TERMINOLOGY. `captured` is this system's name for what the brief calls *paid*:
the money is ours. It is the pre-existing value in the database, the API, the
dashboard and the test suite, so renaming it would break a published contract
to gain nothing — `docs/payments/STATES.md` records the mapping.
"""
from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.payments.models import PaymentStatus

#: Terminal states. Nothing leaves these except a refund, which is itself a
#: move between two of them.
TERMINAL = frozenset(
    {
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
        PaymentStatus.EXPIRED,
        PaymentStatus.REFUNDED,
    }
)

#: States in which the customer's money is ours and an order may be fulfilled.
SETTLED = frozenset({PaymentStatus.CAPTURED, PaymentStatus.PARTIALLY_REFUNDED})

#: States a refund may be issued from.
REFUNDABLE = frozenset({PaymentStatus.CAPTURED, PaymentStatus.PARTIALLY_REFUNDED})

PAYMENT_STATUS_TRANSITIONS = {
    # A fresh payment. `requires_action` is where a redirect/3-D Secure/kiosk
    # reference lands: the customer has somewhere to go but has not paid.
    PaymentStatus.PENDING: {
        PaymentStatus.REQUIRES_ACTION,
        PaymentStatus.AUTHORIZED,
        PaymentStatus.CAPTURED,
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
        PaymentStatus.EXPIRED,
    },
    PaymentStatus.REQUIRES_ACTION: {
        PaymentStatus.AUTHORIZED,
        PaymentStatus.CAPTURED,
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
        PaymentStatus.EXPIRED,
        # A kiosk reference can be re-issued while still unpaid.
        PaymentStatus.REQUIRES_ACTION,
    },
    PaymentStatus.AUTHORIZED: {
        PaymentStatus.CAPTURED,
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
        PaymentStatus.EXPIRED,
    },
    # Captured money can only move towards being given back.
    PaymentStatus.CAPTURED: {
        PaymentStatus.REFUND_PENDING,
        PaymentStatus.PARTIALLY_REFUNDED,
        PaymentStatus.REFUNDED,
    },
    PaymentStatus.REFUND_PENDING: {
        PaymentStatus.PARTIALLY_REFUNDED,
        PaymentStatus.REFUNDED,
        # The provider rejected the refund; the money is still ours.
        PaymentStatus.CAPTURED,
    },
    PaymentStatus.PARTIALLY_REFUNDED: {
        PaymentStatus.REFUND_PENDING,
        PaymentStatus.PARTIALLY_REFUNDED,
        PaymentStatus.REFUNDED,
    },
    PaymentStatus.FAILED: set(),
    PaymentStatus.CANCELLED: set(),
    PaymentStatus.EXPIRED: set(),
    PaymentStatus.REFUNDED: set(),
}


#: Unsettled states. While a payment sits in one of these the gateway may still
#: take the money, so nothing may start a second attempt at it.
IN_FLIGHT = frozenset({PaymentStatus.PENDING, PaymentStatus.REQUIRES_ACTION, PaymentStatus.AUTHORIZED})

#: Gave-up states the gateway can still overrule with a success.
#:
#: Paymob lets a customer retry on the same checkout page: the declined card
#: fails the payment, the next card succeeds minutes later. A success can also
#: land after we expired the payment, or after the order was cancelled while the
#: callback was delayed. In every case the money is ours, and refusing to record
#: it charges the customer for an order we never confirm. Only a verified
#: gateway statement (signed webhook or server-side inquiry) may make this move;
#: see `can_gateway_transition`.
LATE_CAPTURE_FROM = frozenset({PaymentStatus.FAILED, PaymentStatus.EXPIRED, PaymentStatus.CANCELLED})


def can_transition(current: str, new: str) -> bool:
    return new in PAYMENT_STATUS_TRANSITIONS.get(current, set())


def can_gateway_transition(current: str, new: str) -> bool:
    """`can_transition`, plus the one move only the gateway's own word allows."""
    if can_transition(current, new):
        return True
    return new == PaymentStatus.CAPTURED and current in LATE_CAPTURE_FROM


def assert_transition(current: str, new: str) -> None:
    """
    Raise unless `current -> new` is legal.

    Re-entering the same state is always allowed and always a no-op, which is
    what makes a duplicated webhook harmless: the second delivery asks for a
    transition the payment has already made.
    """
    if current == new:
        return
    if not can_transition(current, new):
        raise ApplicationError(
            ErrorCodes.PAYMENT_INVALID_STATE,
            f"Cannot move payment from '{current}' to '{new}'.",
        )


#: How each provider's vocabulary maps onto ours.
#:
#: Providers are not consulted about what these mean — the mapping is ours,
#: it is explicit, and anything a provider sends that is not listed here is
#: treated as unknown rather than guessed at. `None` means "this event carries
#: no status change we act on" (an order-created notification, say), which is
#: different from an unrecognised value.
PROVIDER_STATUS_MAP = {
    "paymob": {
        # Paymob reports a transaction's outcome through success/pending/
        # is_voided/is_refunded booleans; the adapter reduces them to one of
        # these tokens before we get here.
        "captured": PaymentStatus.CAPTURED,
        "authorized": PaymentStatus.AUTHORIZED,
        "pending": PaymentStatus.REQUIRES_ACTION,
        "failed": PaymentStatus.FAILED,
        "voided": PaymentStatus.CANCELLED,
        "refunded": PaymentStatus.REFUNDED,
        "partially_refunded": PaymentStatus.PARTIALLY_REFUNDED,
    },
    # Cash on delivery and InstaPay have no external system to reconcile
    # against: their `get_status` echoes our own value back, because our record
    # is the only record. The mapping is therefore the identity over our own
    # vocabulary rather than a translation of somebody else's.
    "cod": {status: status for status in PaymentStatus.values},
    "instapay": {status: status for status in PaymentStatus.values},
}


#: Statuses an adapter's `create_payment` may report.
#:
#: These are OUR names, not a provider's. `PROVIDER_STATUS_MAP` translates a
#: gateway's webhook vocabulary into ours; a `CreatePaymentResult` is produced by
#: our own adapter and is already in ours. Running one through the other is a
#: category error, and it was a real bug: an adapter reported `requires_action`
#: on creation, that value was looked up in the gateway's webhook table, it was
#: not there, and checkout 500ed on a KeyError.
INITIAL_STATUSES = frozenset(
    {PaymentStatus.PENDING, PaymentStatus.REQUIRES_ACTION, PaymentStatus.AUTHORIZED}
)


def assert_initial_status(provider_code: str, raw_status) -> str:
    """
    Validate the status an adapter reports when it creates a payment.

    Raises rather than defaulting to `pending`: an adapter that reports something
    unexpected has a bug, and quietly calling the payment pending would hide it
    behind a customer who is never sent anywhere to pay.
    """
    status = str(raw_status or PaymentStatus.PENDING)
    if status not in INITIAL_STATUSES:
        raise ValueError(
            f"Adapter '{provider_code}' reported initial status '{status}', which is not one of "
            f"{sorted(INITIAL_STATUSES)}."
        )
    return status


def map_provider_status(provider_code: str, raw_status):
    """
    Translate a provider status token into one of ours.

    Returns `None` when the event carries no status change to apply. Raises
    `KeyError` for a token we have never seen, which the caller turns into a
    recorded-but-unprocessed webhook rather than a guess.
    """
    table = PROVIDER_STATUS_MAP.get(provider_code)
    if table is None:
        raise KeyError(f"No status mapping registered for provider '{provider_code}'.")
    if raw_status is None:
        return None
    key = str(raw_status)
    if key not in table:
        # Providers are inconsistent about case. Normalise before giving up, but
        # still give up rather than guessing at an unfamiliar token.
        upper = key.upper()
        if upper in table:
            return table[upper]
        raise KeyError(f"Unmapped '{provider_code}' status '{raw_status}'.")
    return table[key]
