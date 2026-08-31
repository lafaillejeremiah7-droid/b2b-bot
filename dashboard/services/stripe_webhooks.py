from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone

from django.conf import settings

from dashboard.models import Invoice
from dashboard.services.events import EventIntake, EventIntakeOutcome


class StripeWebhookError(RuntimeError):
    pass


@dataclass(frozen=True)
class StripeWebhookOutcome:
    accepted: bool
    ignored: bool = False
    duplicate: bool = False
    reason: str | None = None


def verify_stripe_signature(
    payload: bytes,
    signature_header: str,
    webhook_secret: str,
    *,
    tolerance_seconds: int = 300,
    now: int | None = None,
) -> None:
    """Verify Stripe's signed payload using the documented v1 HMAC scheme."""
    secret = (webhook_secret or "").strip()
    if not secret:
        raise StripeWebhookError("Stripe webhook verification is not configured.")
    if not signature_header:
        raise StripeWebhookError("Stripe-Signature header is missing.")

    timestamp: int | None = None
    signatures: list[str] = []
    for item in signature_header.split(","):
        key, sep, value = item.strip().partition("=")
        if not sep:
            continue
        if key == "t" and timestamp is None:
            try:
                timestamp = int(value)
            except ValueError as exc:
                raise StripeWebhookError("Stripe-Signature timestamp is invalid.") from exc
        elif key == "v1" and value:
            signatures.append(value)

    if timestamp is None or not signatures:
        raise StripeWebhookError("Stripe-Signature header is incomplete.")

    current = int(time.time()) if now is None else int(now)
    if abs(current - timestamp) > max(0, int(tolerance_seconds)):
        raise StripeWebhookError("Stripe webhook signature timestamp is outside the allowed tolerance.")

    signed_payload = str(timestamp).encode("ascii") + b"." + payload
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise StripeWebhookError("Stripe webhook signature verification failed.")


def _local_invoice(stripe_invoice: dict) -> Invoice | None:
    provider_id = str(stripe_invoice.get("id") or "").strip()
    metadata = stripe_invoice.get("metadata")
    local_id = None
    if isinstance(metadata, dict):
        raw_local_id = metadata.get("local_invoice_id")
        try:
            local_id = int(raw_local_id) if raw_local_id not in (None, "") else None
        except (TypeError, ValueError):
            raise StripeWebhookError("Stripe invoice local_invoice_id metadata is invalid.")

    invoice = None
    if local_id is not None:
        invoice = Invoice.objects.select_related("deal__lead").filter(pk=local_id).first()
        if invoice is None:
            raise StripeWebhookError("Stripe invoice references a local invoice that does not exist yet.")
    elif provider_id:
        invoice = (
            Invoice.objects.select_related("deal__lead")
            .filter(provider_invoice_id=provider_id)
            .first()
        )

    # Events without our metadata/provider identity can belong to unrelated
    # invoices in the same Stripe account. They are acknowledged but ignored.
    if invoice is None:
        return None
    if provider_id and invoice.provider_invoice_id and invoice.provider_invoice_id != provider_id:
        raise StripeWebhookError("Stripe invoice provider identity does not match the local invoice.")
    return invoice


def _whole_dollars(amount_cents) -> int:
    try:
        cents = int(amount_cents)
    except (TypeError, ValueError) as exc:
        raise StripeWebhookError("Stripe invoice amount_paid is invalid.") from exc
    if cents <= 0 or cents % 100:
        raise StripeWebhookError("Stripe invoice payment must resolve to a positive whole-dollar amount.")
    dollars = cents // 100
    if not 1 <= dollars <= 1000:
        raise StripeWebhookError("Stripe invoice payment is outside the supported $1-$1000 range.")
    return dollars


class StripeWebhookIntake:
    """Authenticated Stripe-to-domain event bridge.

    Only ``invoice.paid`` is translated into the domain payment event. Other
    Stripe event types are acknowledged and ignored. The Stripe event ID becomes
    the domain event ID, so retries remain idempotent in ``processed_events``.
    """

    @classmethod
    def handle(cls, payload: bytes, signature_header: str) -> StripeWebhookOutcome:
        verify_stripe_signature(
            payload,
            signature_header,
            settings.STRIPE_WEBHOOK_SECRET,
            tolerance_seconds=settings.STRIPE_WEBHOOK_TOLERANCE_SECONDS,
        )
        try:
            event = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StripeWebhookError("Stripe webhook body is not valid JSON.") from exc
        if not isinstance(event, dict):
            raise StripeWebhookError("Stripe webhook body must be a JSON object.")

        event_id = str(event.get("id") or "").strip()
        event_type = str(event.get("type") or "").strip()
        if not event_id or len(event_id) > 128:
            raise StripeWebhookError("Stripe event ID is missing or too long.")
        if event_type != "invoice.paid":
            return StripeWebhookOutcome(True, ignored=True, reason=f"ignored Stripe event type {event_type or 'unknown'}")

        data = event.get("data")
        stripe_invoice = data.get("object") if isinstance(data, dict) else None
        if not isinstance(stripe_invoice, dict):
            raise StripeWebhookError("Stripe invoice.paid event is missing data.object.")
        if str(stripe_invoice.get("status") or "").strip() != "paid":
            raise StripeWebhookError("Stripe invoice.paid payload does not report status=paid.")

        invoice = _local_invoice(stripe_invoice)
        if invoice is None:
            return StripeWebhookOutcome(True, ignored=True, reason="invoice.paid is not for a B2B Bot invoice")

        currency = str(stripe_invoice.get("currency") or "").strip().lower()
        expected_currency = str(settings.STRIPE_CURRENCY or "").strip().lower()
        if not currency or currency != expected_currency:
            raise StripeWebhookError(
                f"Stripe invoice currency {currency or 'missing'} does not match configured currency {expected_currency or 'missing'}."
            )
        amount_usd = _whole_dollars(stripe_invoice.get("amount_paid"))

        try:
            created = int(event.get("created"))
        except (TypeError, ValueError) as exc:
            raise StripeWebhookError("Stripe event created timestamp is invalid.") from exc
        event_timestamp = datetime.fromtimestamp(created, tz=dt_timezone.utc).isoformat()
        outcome: EventIntakeOutcome = EventIntake.handle(
            {
                "event_id": event_id,
                "event_type": "payment_received",
                "lead_id": invoice.deal.lead_id,
                "deal_id": invoice.deal_id,
                "amount": amount_usd,
                "event_timestamp": event_timestamp,
                "provider": "stripe",
                "provider_invoice_id": stripe_invoice.get("id"),
            }
        )
        if not outcome.accepted:
            return StripeWebhookOutcome(False, reason=outcome.rejection_reason)
        return StripeWebhookOutcome(True, duplicate=outcome.duplicate)
