# Notifications

Files: `apps/notifications/models.py`, `apps/notifications/services.py`,
`apps/notifications/tasks.py`, `apps/notifications/providers/`
(`sms_base.py`, `whatsapp_base.py`), `templates/emails/`.

## Channels

Four notification channels are modeled (`NotificationChannel` choices):
`email`, `sms`, `whatsapp`, `push`, `in_app`. Only email, SMS, WhatsApp, and
in-app are actually wired to a sending path in this codebase; push has no
provider implementation.

### Email — implemented

`apps.notifications.services.send_email_now(to, subject, template_name, context)`:
renders `templates/emails/{template_name}.html` via Django's template loader,
with a best-effort `.txt` fallback (falls back to the HTML body if no `.txt`
template exists), and sends via `EmailMultiAlternatives` using
`EMAIL_BACKEND` from settings (`config/settings/base.py`; defaults to
`django.core.mail.backends.console.EmailBackend` — prints to console/log in
development, must be set to a real SMTP backend + `EMAIL_HOST`/
`EMAIL_HOST_USER`/`EMAIL_HOST_PASSWORD` in production). Every call injects
`site_name` (`SITE_NAME`) and `frontend_url` (`FRONTEND_URL`) into the
template context automatically.

Templates that exist today under `templates/emails/`: `_base.html` (shared
layout), `welcome.html`, `verify_email.html`, `reset_password.html`,
`order_confirmation.html`, `payment_confirmation.html`,
`payment_failed.html`, `order_shipped.html`, `order_delivered.html`,
`refund.html`, `abandoned_cart.html`, `promotion.html` — matching every
`send_email_task.delay(...)` call site across `apps/users`, `apps/checkout`,
`apps/payments`, `apps/orders`, and `apps/notifications` itself.

### SMS — provider abstraction, console default

`apps.notifications.providers.sms_base.SMSProvider` (ABC):

```python
class SMSProvider(ABC):
    def send(self, to: str, message: str) -> dict:
        """Returns {"success": bool, "provider_message_id": str | None, "raw": Any}."""
```

`ConsoleSMSProvider` is the only concrete implementation present — it logs
the message instead of sending it (`logger.info("[SMS -> %s] %s", to, message)`)
and always returns success. `settings.SMS_PROVIDER` (`.env`: `console` |
`vonage` | `twilio` | `msegat`) selects the provider; only `console` has a
class behind it today. Wiring a real vendor means adding a new
`apps/notifications/providers/<vendor>.py` implementing `SMSProvider.send()`
against that vendor's actual API and registering it in the provider factory
— the same "don't fabricate an untested integration" policy as payment/
shipping providers.

### WhatsApp — order updates through Meta's WhatsApp Cloud API

`apps.notifications.providers.whatsapp_base.WhatsAppProvider` (ABC,
`send_template(to, template_name, params)`) has two implementations, chosen by
`WHATSAPP_PROVIDER`:

- `console` — logs instead of sending (development, tests).
- `meta_cloud_api` — `providers/meta_cloud.py`, Meta's official Cloud API:
  `POST https://graph.facebook.com/<WHATSAPP_GRAPH_API_VERSION>/<WHATSAPP_PHONE_NUMBER_ID>/messages`
  with `Authorization: Bearer <WHATSAPP_ACCESS_TOKEN>`, `type: template`, the
  template's body parameters filled in order. A 200 returns the `wamid.…`
  message id, stored as `NotificationLog.provider_message_id`. A 200 means
  Meta accepted the message; delivered/read/failed statuses come later on a
  webhook this shop does not subscribe to yet.

Unofficial WhatsApp Web automation is not offered: it violates WhatsApp's terms
and gets the number banned.

**What is sent, and when** (`apps.orders.services.STATUS_WHATSAPP`, plus the
checkout transfer path):

| Message key | When | Event logged | Default parameters |
|---|---|---|---|
| `order_awaiting_payment` | Transfer (InstaPay / Vodafone Cash) or COD-deposit order placed | `order_created` | customer_name, order_number, amount, pay_to |
| `order_confirmed` | Order moves to CONFIRMED (COD at checkout; online/transfer once paid) | `order_created` | customer_name, order_number, total |
| `order_shipped` | → SHIPPED | `order_shipped` | customer_name, order_number |
| `order_out_for_delivery` | → OUT_FOR_DELIVERY (no email for this one) | `out_for_delivery` | customer_name, order_number |
| `order_delivered` | → DELIVERED | `order_delivered` | customer_name, order_number |
| `order_cancelled` | → CANCELLED (the staff reason is never included) | `order_cancelled` | customer_name, order_number |

A card order that is never paid sends nothing. Guest and signed-in orders are
treated alike: the message goes to `Order.customer_phone`.

Each message is an approved Utility template named in
`WHATSAPP_TEMPLATE_<MESSAGE>`; empty (the default) switches it off.
`WHATSAPP_TEMPLATE_<MESSAGE>_PARAMS` lists the fields filling `{{1}}, {{2}}, …`
— it must match the template as approved. Fields: `customer_name`,
`first_name`, `order_number`, `total` ("450.00 ج.م"), `order_url`, and for
`order_awaiting_payment` also `amount`, `pay_to`, `account_name`, `reference`.
A field name the message cannot fill is logged as an error and that message is
skipped; the order is never affected. `WHATSAPP_TEMPLATE_LANGUAGE` must match
the template's approved language code exactly.

**Path:** `queue_order_whatsapp` → `notifications.whatsapp.queue_whatsapp_template`
→ `transaction.on_commit` → `send_whatsapp_task` (Celery) → provider. Like
email, a rolled-back transaction sends nothing and a broker outage costs the
message, not the order. The task normalises the number to E.164
(`notifications.whatsapp.to_e164`: `01001234567`, `201001234567`,
`0020…`, Arabic digits, Gulf numbers; landlines refused) and writes a
`NotificationLog` row for every attempt, with Meta's error code and message on
failure. Only throttling, Meta-side outages and connection failures are
retried; a wrong template, a number not on WhatsApp, an expired token, or a
request that timed out after being sent (it may have been delivered) are not.
A `meta_cloud_api` provider without its id/token logs a failure on each send
instead of raising.

### In-app notifications

`apps.notifications.models.Notification` — a per-user feed row (`event`,
`title`, `body`, `data` JSON, `read_at`), created via
`apps.notifications.services.create_in_app_notification()` or asynchronously
via `create_in_app_notification_task`. Indexed on `(user, read_at)` for
unread-count queries.

## Delivery audit trail

Every outbound send — regardless of channel or success/failure — is logged
to `apps.notifications.models.NotificationLog` via
`apps.notifications.services.log_delivery()`: `event`, `channel`, `recipient`,
`success`, `provider_message_id`, `error_message`. This is the audit record
answering "did this email/SMS actually go out, and what happened."

## Async dispatch via Celery

All sends are dispatched through Celery tasks
(`apps.notifications.tasks.send_email_task` /
`send_sms_task` / `send_whatsapp_task`), never called synchronously from a
request-handling view (aside from the COD checkout path calling into
`confirm_order_after_payment`, which itself only *queues* — `.delay(...)` —
the confirmation emails, never sends inline). Each task:

- is `@shared_task(bind=True, max_retries=3, default_retry_delay=30)` —
  automatically retries up to 3 times, 30 seconds apart, on failure
- calls the corresponding `send_*_now()` service function
- logs the outcome via `log_delivery()` regardless of success/failure
- routes to the dedicated `notifications` Celery queue
  (`CELERY_TASK_ROUTES["apps.notifications.tasks.*"]` in
  `config/settings/base.py`), isolated from `default`/`inventory`/
  `analytics`/`payments` traffic

This means a burst of transactional emails (order confirmations) cannot
delay time-sensitive tasks on other queues (e.g. stock reservation release),
and a transient SMTP outage automatically self-heals via retry rather than
silently dropping the notification.

## Notification preferences

`apps.notifications.models.NotificationPreference` — one-to-one per user,
opt-in/opt-out flags per channel (`marketing_email`, `marketing_sms`,
`marketing_whatsapp`, `marketing_push`) for **non-transactional** (marketing)
sends only; transactional notifications (order confirmation, password reset,
OTP) are not gated by these preferences in the code reviewed.

## Celery Beat schedule (notification-related tasks)

From `config/settings/base.py` `CELERY_BEAT_SCHEDULE`:

| Task | Interval | What it does |
|---|---|---|
| `apps.notifications.tasks.notify_expiration_alerts_task` | daily (86400s) | Iterates `apps.inventory.services.get_batches_nearing_expiration()` (7/14/30-day thresholds — `settings.EXPIRATION_ALERT_DAYS`) and logs an alert per batch. Currently logs only (`logger.info`); the integration point for a real staff-facing alert. |
| `apps.notifications.tasks.process_abandoned_carts_task` | hourly (3600s) | Calls `apps.carts.services.get_abandoned_carts()` (carts inactive past `ABANDONED_CART_HOURS`, default 6) and, for each cart with a known user email, queues the `abandoned_cart` email exactly once (`cart.abandoned_notified_at` is stamped after sending so the same cart is never re-notified). |
| `apps.subscriptions.tasks.process_due_subscriptions_task` | daily | Places orders for subscriptions whose `next_order_date` has arrived (subscription billing — see `apps/subscriptions/models.py`). |
| `apps.payments.tasks.reconcile_pending_payments_task` | every 15 min | Not itself a notification task, but its `mark_payment_failed()` path triggers the `payment_failed` email — see `docs/architecture/payments.md`. |

`notify_low_stock_task` (triggered inline from
`apps.inventory.services._maybe_trigger_low_stock_alert`, not on a Beat
schedule — it fires immediately whenever a `reserve_stock()` call crosses the
low-stock/out-of-stock threshold) currently only logs, same as the
expiration alert task.
