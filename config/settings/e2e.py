"""
Settings for end-to-end runs against a real server.

Development settings, with the rate limits raised.

WHY THIS EXISTS. The anonymous throttle is 60 requests a minute, and a single
browse of six pages in this storefront costs 27 API calls — so a full
purchase run (browse, filter, search, product, cart, register, address,
shipping, totals, order, history, cancel) cannot finish inside one minute
without being throttled. That is the limit doing its job; it is not something
the test should be allowed to switch off in development or production, and it
is not a reason to weaken the real number.

So the rates move only here, and only for a settings module you have to ask
for by name:

    DJANGO_SETTINGS_MODULE=config.settings.e2e python manage.py runserver

DEBUG stays on and the database is the development one. Never point a
deployment at this.
"""
from .development import *  # noqa: F401,F403

REST_FRAMEWORK = {  # noqa: F405
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_THROTTLE_RATES": {
        # Generous enough that a scripted run never trips them, low enough
        # that a genuinely runaway loop in a test still fails loudly rather
        # than hammering the box forever.
        key: "2000/min"
        for key in REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]  # noqa: F405
    },
}
