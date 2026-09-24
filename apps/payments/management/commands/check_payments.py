"""
`python manage.py check_payments`

Answers, for an operator standing in front of a deployment: which payment
methods will customers actually see, and for each one that is missing, exactly
which setting is empty.

This exists because "is Paymob live?" has three different answers — the flag is
set, the credentials are complete, and the provider has approved the account —
and only the first two are visible from inside the process. It prints both, and
says plainly that the third is not something it can check.

Exit code 1 when nothing at all is available, because a shop that cannot take a
single payment should fail a deployment smoke test rather than come up quietly.

Some of what it prints is Arabic — the manual refund notes staff read. Writing
Arabic to a console that cannot encode it must degrade the characters, never the
report: see `_writer`.
"""
from django.core.management.base import BaseCommand

from apps.payments import config as payment_config
from apps.payments.webhooks import all_webhook_urls


def _writer(wrapper):
    """
    Return a write function that cannot be killed by the console's encoding.

    Windows terminals still default to cp1252, which has no Arabic. This command
    prints Arabic refund notes, so on a default `cmd.exe` it died with a
    `UnicodeEncodeError` instead of reporting anything — and this is precisely
    the command an operator runs when payments are already suspect. The test
    suite never saw it because pytest captures output through an in-memory
    stream that accepts any string.

    Prefer switching the stream to UTF-8 so the Arabic is actually readable. If
    the stream cannot be reconfigured, replace the characters it cannot encode
    and keep going: a report with a few question marks in it is worth far more
    than a traceback.
    """
    stream = getattr(wrapper, "_out", None)
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
            return wrapper.write
        except (ValueError, OSError, LookupError):
            pass

    encoding = getattr(stream, "encoding", None)
    if not encoding:
        # An in-memory stream (tests): it takes any str already.
        return wrapper.write

    def write(msg="", *args, **kwargs):
        try:
            msg.encode(encoding)
        except (UnicodeEncodeError, LookupError):
            msg = msg.encode(encoding, "replace").decode(encoding, "replace")
        return wrapper.write(msg, *args, **kwargs)

    return write


class Command(BaseCommand):
    help = "Report which payment methods are available, and what is missing from the rest."

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit non-zero if any method is unavailable, not just if all are.",
        )

    def handle(self, *args, **options):
        out = _writer(self.stdout)
        err = _writer(self.stderr)
        available = []
        unavailable = []

        # One row per method, not per route: InstaPay has two routes and listing
        # it twice made the report look broken.
        for method in payment_config.method_codes():
            state = payment_config.availability(method)
            (available if state.available else unavailable).append((state.spec, state))

        out(self.style.MIGRATE_HEADING("\nAvailable to customers"))
        if not available:
            out(self.style.ERROR("  none — this shop cannot take a payment"))
        for spec, _state in available:
            can_refund, note = payment_config.refund_available(spec.method)
            refund = "refundable via API" if can_refund and not note else (note or "refunds unavailable")
            out(
                f"  {self.style.SUCCESS('OK')}  {spec.method:<14} "
                f"route={spec.gateway:<9} {refund}"
            )

        out(self.style.MIGRATE_HEADING("\nNot available"))
        for spec, state in unavailable:
            out(f"  {self.style.WARNING('--')}  {spec.method:<14} {state.reason}")

        out(self.style.MIGRATE_HEADING("\nWebhook URLs to register with each provider"))
        for code, url in all_webhook_urls().items():
            out(f"  {code:<8} {url}")
        out(
            "  (from BACKEND_PUBLIC_URL — if that looks like localhost, the URLs you "
            "register will too)"
        )

        out(self.style.MIGRATE_HEADING("\nWhat this command cannot tell you"))
        out(
            "  Whether the provider has approved the account, whether the keys are live\n"
            "  or test, and whether the webhook URL above is actually registered in the\n"
            "  provider's dashboard. See docs/payments/PROVIDERS.md."
        )

        if not available:
            err(self.style.ERROR("\nNo payment method is available."))
            raise SystemExit(1)
        if options["strict"] and unavailable:
            err(
                self.style.ERROR(f"\n{len(unavailable)} method(s) unavailable (--strict).")
            )
            raise SystemExit(1)
        out("")
