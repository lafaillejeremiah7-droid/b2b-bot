from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings


class StripeInvoiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class StripeInvoiceReceipt:
    provider_invoice_id: str
    invoice_number: str | None
    hosted_invoice_url: str | None


class StripeInvoiceClient:
    """Minimal Stripe REST client used by Closer to generate an invoice link.

    This client deliberately does *not* call Stripe's ``/send`` endpoint. Stripe
    creates/finalizes the invoice and hosts the secure payment page; the Sales
    Bot owns customer email delivery after the operator approves it in the
    dashboard.
    """

    api_base = "https://api.stripe.com/v1"

    def __init__(self, secret_key: str | None = None, timeout: int | None = None):
        self.secret_key = (secret_key if secret_key is not None else settings.STRIPE_SECRET_KEY).strip()
        self.timeout = timeout or settings.STRIPE_API_TIMEOUT_SECONDS

    def _post(self, path: str, data: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        if not self.secret_key:
            raise StripeInvoiceError(
                "Stripe is not configured. Set STRIPE_SECRET_KEY in the private deployment environment."
            )
        token = base64.b64encode(f"{self.secret_key}:".encode("utf-8")).decode("ascii")
        body = urlencode({k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in data.items()}).encode("utf-8")
        request = Request(
            f"{self.api_base}{path}",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Basic {token}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Idempotency-Key": idempotency_key[:255],
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message")
            except Exception:
                detail = None
            raise StripeInvoiceError((detail or f"Stripe returned HTTP {exc.code}.")[:500]) from exc
        except (URLError, TimeoutError) as exc:
            raise StripeInvoiceError("Stripe invoice request could not be completed.") from exc
        if not isinstance(payload, dict):
            raise StripeInvoiceError("Stripe returned an invalid invoice response.")
        return payload

    def create_invoice_link(
        self,
        *,
        local_invoice_id: int,
        recipient_email: str,
        customer_name: str,
        amount_usd: int,
        description: str,
    ) -> StripeInvoiceReceipt:
        """Create and finalize an invoice, returning its hosted payment URL.

        Every operation uses a deterministic idempotency key derived from the
        local invoice ID, so retries collapse onto the same Stripe resources.
        Finalizing makes the Hosted Invoice Page available. Emailing that link is
        intentionally outside Stripe and is handled by Sales Bot.
        """
        base_key = f"b2b-invoice-{local_invoice_id}"
        customer = self._post(
            "/customers",
            {"email": recipient_email, "name": customer_name or recipient_email},
            idempotency_key=f"{base_key}-customer",
        )
        customer_id = str(customer.get("id") or "")
        if not customer_id:
            raise StripeInvoiceError("Stripe did not return a customer ID.")

        invoice = self._post(
            "/invoices",
            {
                "customer": customer_id,
                "collection_method": "send_invoice",
                "days_until_due": settings.STRIPE_INVOICE_DAYS_UNTIL_DUE,
                "auto_advance": False,
                "description": description,
                "metadata[local_invoice_id]": local_invoice_id,
            },
            idempotency_key=f"{base_key}-draft",
        )
        stripe_invoice_id = str(invoice.get("id") or "")
        if not stripe_invoice_id:
            raise StripeInvoiceError("Stripe did not return an invoice ID.")

        self._post(
            "/invoiceitems",
            {
                "customer": customer_id,
                "invoice": stripe_invoice_id,
                "amount": amount_usd * 100,
                "currency": settings.STRIPE_CURRENCY,
                "description": description,
            },
            idempotency_key=f"{base_key}-item",
        )
        finalized = self._post(
            f"/invoices/{stripe_invoice_id}/finalize",
            {},
            idempotency_key=f"{base_key}-finalize",
        )
        hosted_url = str(finalized.get("hosted_invoice_url") or "").strip()
        if not hosted_url:
            raise StripeInvoiceError("Stripe finalized the invoice but did not return a hosted invoice URL.")
        return StripeInvoiceReceipt(
            provider_invoice_id=str(finalized.get("id") or stripe_invoice_id),
            invoice_number=(str(finalized["number"]) if finalized.get("number") else None),
            hosted_invoice_url=hosted_url,
        )


def get_stripe_invoice_client() -> StripeInvoiceClient:
    return StripeInvoiceClient()
