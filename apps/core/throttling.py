"""
Throttles that tell browsing apart from everything else.

The global anonymous limit (60/min per IP) counted every request, and one
storefront page load makes about a dozen API calls — so five page loads a
minute from one IP, which on an Egyptian carrier's shared (CGNAT) address is
several customers at once, blanked the shop with 429s. Public read-only
endpoints now declare `throttle_scope = "catalog"` and are counted only by that
scope's generous budget; logins, checkout, guest order lookup, coupons, chat
and every write keep the strict anonymous limit plus their own scopes.
"""
from rest_framework.permissions import SAFE_METHODS
from rest_framework.throttling import AnonRateThrottle

#: Scopes whose safe-method requests are budgeted by the scope alone.
BROWSING_SCOPES = frozenset({"catalog"})


def is_browsing(request, view) -> bool:
    return request.method in SAFE_METHODS and getattr(view, "throttle_scope", None) in BROWSING_SCOPES


class BrowsingAwareAnonRateThrottle(AnonRateThrottle):
    """The anonymous baseline, except for reads that a browsing scope already budgets."""

    def allow_request(self, request, view):
        if is_browsing(request, view):
            return True
        return super().allow_request(request, view)
