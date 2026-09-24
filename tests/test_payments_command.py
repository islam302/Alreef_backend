"""
`python manage.py check_payments` — the operator's answer to "can we take money?".

This file exists because of a bug it would have caught. The command prints the
Arabic manual-refund notes, and Django writes them straight to the console. On a
default Windows terminal (cp1252, no Arabic) the command died with a
`UnicodeEncodeError` and printed no report at all — in the one situation where an
operator reaches for it, which is when payments are already suspect.

The rest of the suite never saw it because pytest captures output through an
in-memory stream that accepts any string. So the two encoding tests here
deliberately avoid the captured stream and hand the command cp1252 streams
instead — one that can be switched to UTF-8, and one that refuses, because those
take different paths and only the second degrades the text.
"""
import io

import pytest
from django.core.management import call_command

from apps.payments import config as payment_config
from tests.conftest import (
    INSTAPAY_TEST_CONFIG,
    PAYMOB_TEST_CONFIG,
    payment_providers_with,
)


def run(**options):
    """Run the command, returning its stdout as text."""
    out = io.StringIO()
    err = io.StringIO()
    call_command("check_payments", stdout=out, stderr=err, **options)
    return out.getvalue()


def test_cod_is_reported_available_with_no_gateway_configured():
    """
    The default deployment can still take money.

    A shop with no gateway credentials at all must not look broken: cash on
    delivery needs no configuration, so it is listed, and the command exits 0.
    """
    report = run()

    assert "cod" in report
    assert "route=cod" in report
    assert "this shop cannot take a payment" not in report


def test_unavailable_methods_name_the_setting_that_is_missing():
    """
    "Not available" is useless without "because of what".

    An operator reading this needs the variable name to go and set, not a
    sentiment. `PAYMOB_ENABLED` is the first gate, so it is what they are told.
    """
    report = run()

    assert "PAYMOB_ENABLED is not set." in report
    for method in ("card", "mobile_wallet"):
        assert method in report


def test_instapay_is_listed_once_and_blames_both_routes(settings):
    """
    InstaPay has two possible routes, and one row.

    It was listed twice — once per route — which made the report look like a bug.
    When neither route works, the single row has to account for both, or the
    operator sets the variable the report named and nothing changes.
    """
    report = run()

    instapay_rows = [ln for ln in report.splitlines() if "instapay" in ln and "route=" not in ln]
    assert len(instapay_rows) == 1, instapay_rows
    assert "[paymob]" in instapay_rows[0]
    assert "[instapay]" in instapay_rows[0]


def test_instapay_reports_the_manual_route_when_paymob_has_no_integration_id(settings):
    """Paymob lists InstaPay as coming soon, so the manual transfer route serves it."""
    settings.PAYMENT_PROVIDERS = payment_providers_with(
        PAYMOB=PAYMOB_TEST_CONFIG, INSTAPAY=INSTAPAY_TEST_CONFIG
    )

    report = run()

    assert "instapay       route=instapay" in report


def test_instapay_switches_to_paymob_when_the_integration_id_appears(settings):
    """
    The promise that this costs no code change when Paymob ships InstaPay.

    Setting the integration id has to move the method onto the gateway route —
    and must not leave it offered twice while both routes are viable.
    """
    settings.PAYMENT_PROVIDERS = payment_providers_with(
        PAYMOB={**PAYMOB_TEST_CONFIG, "INTEGRATION_ID_INSTAPAY": "4009"},
        INSTAPAY=INSTAPAY_TEST_CONFIG,
    )

    report = run()

    assert "instapay       route=paymob" in report
    assert "route=instapay" not in report
    assert len([ln for ln in report.splitlines() if "instapay" in ln]) == 1


def test_the_report_lists_the_webhook_url_to_register(settings):
    """
    Half of "is it live?" is whether the provider is pointed at us.

    The command cannot verify the registration, so the least it can do is print
    the exact URL to paste — including when it is localhost, which it says.
    """
    settings.BACKEND_PUBLIC_URL = "https://api.example.test"

    report = run()

    assert "https://api.example.test/api/v1/payments/webhooks/paymob/" in report
    assert "BACKEND_PUBLIC_URL" in report


def test_only_paymob_has_a_webhook_url(settings):
    """
    COD and InstaPay are settled by a person, so they have no webhook.

    If either appeared here an operator might go looking for somewhere to
    register it, and a URL that accepted such a POST could only be hostile.
    """
    settings.PAYMENT_PROVIDERS = payment_providers_with(
        PAYMOB=PAYMOB_TEST_CONFIG, INSTAPAY=INSTAPAY_TEST_CONFIG
    )

    report = run()
    webhook_block = report.split("Webhook URLs")[1].split("What this command")[0]

    assert "paymob" in webhook_block
    assert "instapay" not in webhook_block
    assert "cod" not in webhook_block


def test_it_refuses_to_come_up_quietly_with_no_payment_method(settings):
    """A shop that cannot take a single payment should fail a deployment smoke test."""
    settings.PAYMENT_PROVIDERS = payment_providers_with(
        COD={**settings.PAYMENT_PROVIDERS["COD"], "ENABLED": False}
    )

    with pytest.raises(SystemExit) as exit_info:
        run()

    assert exit_info.value.code == 1


def test_strict_fails_when_any_method_is_unavailable():
    """
    `--strict` is for a deployment that intends to offer everything.

    Without a gateway configured, the default run passes and the strict run does
    not — that difference is the whole point of the flag.
    """
    run()  # the same configuration, not strict, does not raise

    with pytest.raises(SystemExit) as exit_info:
        run(strict=True)

    assert exit_info.value.code == 1


def test_strict_passes_when_every_method_is_available(settings):
    settings.PAYMENT_PROVIDERS = payment_providers_with(
        PAYMOB={**PAYMOB_TEST_CONFIG, "INTEGRATION_ID_INSTAPAY": "4009"},
        INSTAPAY=INSTAPAY_TEST_CONFIG,
    )

    report = run(strict=True)

    assert "Not available" in report
    for method in payment_config.method_codes():
        assert method in report


def test_no_removed_gateway_appears_in_the_report():
    """Fawry, valU, Sympl and Aman were removed. Nothing should still advertise them."""
    report = run().lower()

    for gone in ("fawry", "valu", "sympl", "aman"):
        assert gone not in report


class StubbornStream:
    """
    A text stream that reports a cp1252 encoding and refuses to be reconfigured.

    Some wrapped streams cannot switch encoding. This is the case the fallback in
    `_writer` exists for, and the only way to exercise it is to hand the command
    a stream that says no.
    """

    encoding = "cp1252"

    def __init__(self):
        self.written = []

    def write(self, text):
        # The real failure: the stream encodes on write, and raises on Arabic.
        text.encode(self.encoding)
        self.written.append(text)
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False


def test_a_console_that_cannot_encode_arabic_still_gets_a_report(settings):
    """
    The regression, on the stream that cannot be rescued.

    Arabic refund notes are printed for the manually-settled methods, so this
    configures both of them and writes to a cp1252 stream that refuses to switch
    encoding. The requirement is that the characters degrade and the report
    survives, not the other way round: an operator checking payments during an
    incident must still learn which methods are live.
    """
    settings.PAYMENT_PROVIDERS = payment_providers_with(
        PAYMOB=PAYMOB_TEST_CONFIG, INSTAPAY=INSTAPAY_TEST_CONFIG
    )

    console = StubbornStream()
    call_command("check_payments", stdout=console, stderr=console)
    report = "".join(console.written)

    # The facts an operator came for survived the encoding.
    assert "route=instapay" in report
    assert "route=cod" in report
    assert "card" in report
    # The Arabic could not be encoded, so it degraded rather than crashing.
    assert "?" in report


def test_a_reconfigurable_console_is_switched_to_utf8_rather_than_degraded(settings):
    """
    Degrading characters is the last resort, not the first.

    Most streams — including a real Windows console on Python 3 — can be switched
    to UTF-8, and then the Arabic arrives readable. Preferring that is why the
    fix did not simply replace every character it was unsure about.
    """
    settings.PAYMENT_PROVIDERS = payment_providers_with(
        PAYMOB=PAYMOB_TEST_CONFIG, INSTAPAY=INSTAPAY_TEST_CONFIG
    )

    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1252", newline="")

    call_command("check_payments", stdout=console, stderr=console)
    console.flush()

    _can_refund, note = payment_config.refund_available("instapay")
    report = raw.getvalue().decode("utf-8")
    assert note and note in report


def test_the_arabic_refund_note_is_intact_on_a_utf8_console(settings):
    """
    Degrading the characters is the fallback, not the behaviour.

    On a console that can encode Arabic, the note has to arrive readable —
    otherwise the fix for the crash would have quietly destroyed the message.
    """
    settings.PAYMENT_PROVIDERS = payment_providers_with(
        PAYMOB=PAYMOB_TEST_CONFIG, INSTAPAY=INSTAPAY_TEST_CONFIG
    )

    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="utf-8", newline="")

    call_command("check_payments", stdout=console, stderr=console)
    console.flush()
    report = raw.getvalue().decode("utf-8")

    _can_refund, note = payment_config.refund_available("instapay")
    assert note and note in report
    assert "?" not in note
