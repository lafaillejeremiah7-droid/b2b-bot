from __future__ import annotations

import concurrent.futures
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping
from uuid import UUID

from django.conf import settings

from dashboard.models import AdapterInvocation, AdapterOperationName, AdapterResultStatus

from .yahoo_smtp import YahooSMTPError, get_yahoo_smtp_client, yahoo_smtp_configured


@dataclass(frozen=True)
class AdapterResult:
    status: Literal["success", "failure"]
    failure_reason: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.status not in {"success", "failure"}:
            raise ValueError("adapter status must be success or failure")
        if self.status == "failure" and not self.failure_reason:
            raise ValueError("failure result requires failure_reason")
        if self.failure_reason and not 1 <= len(self.failure_reason) <= 500:
            raise ValueError("failure_reason must hold 1 to 500 characters")


class PipelineAdapter(ABC):
    @abstractmethod
    def generate_site_preview(self, *, lead_id: int, idempotency_key: UUID) -> AdapterResult: ...

    @abstractmethod
    def send_prospect_email(
        self,
        *,
        lead_id: int,
        to_email: str,
        subject: str,
        body: str,
        idempotency_key: UUID,
    ) -> AdapterResult: ...

    @abstractmethod
    def send_delivery_email(
        self,
        *,
        deal_id: int,
        to_email: str,
        archive_link: str,
        idempotency_key: UUID,
    ) -> AdapterResult: ...

    @abstractmethod
    def create_invoice(self, *, deal_id: int, amount_usd: int, idempotency_key: UUID) -> AdapterResult: ...

    @abstractmethod
    def log_outbound_call(
        self,
        *,
        lead_id: int,
        outcome: str,
        notes: str,
        idempotency_key: UUID,
    ) -> AdapterResult: ...


class StubPipelineAdapter(PipelineAdapter):
    """Deterministic no-network implementation used by the dashboard in stub mode."""

    def generate_site_preview(self, *, lead_id: int, idempotency_key: UUID) -> AdapterResult:
        return AdapterResult("success", payload={"lead_id": lead_id, "stub": True})

    def send_prospect_email(self, **kwargs) -> AdapterResult:
        return AdapterResult("success", payload={"stub": True})

    def send_delivery_email(self, **kwargs) -> AdapterResult:
        return AdapterResult("success", payload={"stub": True})

    def create_invoice(self, **kwargs) -> AdapterResult:
        return AdapterResult("success", payload={"stub": True, "draft_only": True})

    def log_outbound_call(self, **kwargs) -> AdapterResult:
        return AdapterResult("success", payload={"stub": True})


class LivePipelineAdapter(StubPipelineAdapter):
    """Live provider boundary.

    Prospect and delivery email are submitted through the configured business
    Yahoo mailbox over SMTP. Invoice creation remains a local-draft operation;
    Stripe is touched only by InvoiceSendGate after operator approval.
    Unconfigured operations fail closed rather than pretending to run.
    """

    @staticmethod
    def _not_configured(operation: str = "live adapter provider") -> AdapterResult:
        return AdapterResult("failure", failure_reason=f"{operation} is not configured")

    @staticmethod
    def _yahoo_ready() -> bool:
        return yahoo_smtp_configured()

    @classmethod
    def _send_yahoo(
        cls,
        *,
        to_email: str,
        subject: str,
        body: str,
        idempotency_key: UUID | str,
    ) -> AdapterResult:
        if not cls._yahoo_ready():
            return cls._not_configured("Yahoo business SMTP sender")
        try:
            receipt = get_yahoo_smtp_client().send(
                to=to_email,
                subject=subject,
                body=body,
                idempotency_key=idempotency_key,
            )
        except (YahooSMTPError, ValueError) as exc:
            return AdapterResult("failure", failure_reason=str(exc)[:500])
        return AdapterResult("success", payload={"message_id": receipt.message_id})

    def generate_site_preview(self, **kwargs) -> AdapterResult:
        return self._not_configured("site-preview provider")

    def send_prospect_email(self, **kwargs) -> AdapterResult:
        return self._send_yahoo(
            to_email=str(kwargs.get("to_email") or ""),
            subject=str(kwargs.get("subject") or ""),
            body=str(kwargs.get("body") or ""),
            idempotency_key=kwargs.get("idempotency_key") or "",
        )

    def send_delivery_email(self, **kwargs) -> AdapterResult:
        archive_link = str(kwargs.get("archive_link") or "").strip()
        if not archive_link:
            return AdapterResult("failure", failure_reason="delivery archive link is missing")
        sender_name = settings.OUTREACH_SENDER_NAME.strip() or "Website Design Team"
        body = (
            "Hi,\n\n"
            "Your website delivery is ready. You can access the final delivery here:\n\n"
            f"{archive_link}\n\n"
            "Thank you for working with us.\n\n"
            f"Best,\n{sender_name}\nWebsite Design & Digital Presence"
        )
        return self._send_yahoo(
            to_email=str(kwargs.get("to_email") or ""),
            subject="Your website delivery is ready",
            body=body,
            idempotency_key=kwargs.get("idempotency_key") or "",
        )

    def create_invoice(self, **kwargs) -> AdapterResult:
        # Creating an invoice in the Deal Room must never email a customer or
        # touch Stripe. It only permits InvoiceManager to persist the local draft.
        return AdapterResult("success", payload={"draft_only": True})

    def log_outbound_call(self, **kwargs) -> AdapterResult:
        return self._not_configured("outbound-call provider")


def _jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class TimeoutEnforcingAdapter(PipelineAdapter):
    def __init__(self, implementation: PipelineAdapter):
        self.implementation = implementation

    def _invoke(self, operation: AdapterOperationName, fn, **kwargs) -> AdapterResult:
        key = kwargs.get("idempotency_key")
        if not isinstance(key, UUID):
            raise TypeError("every adapter invocation requires a UUID idempotency_key")

        started = time.monotonic()
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(fn, **kwargs)
        try:
            result = future.result(timeout=settings.ADAPTER_OPERATION_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            future.cancel()
            result = AdapterResult(
                "failure",
                failure_reason=(
                    f"{operation.value} did not return within "
                    f"{settings.ADAPTER_OPERATION_TIMEOUT_SECONDS}s"
                )[:500],
            )
        except Exception as exc:
            result = AdapterResult(
                "failure",
                failure_reason=f"adapter failure: {type(exc).__name__}"[:500],
            )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        AdapterInvocation.objects.create(
            operation_name=operation,
            arguments=_jsonable({k: v for k, v in kwargs.items() if k != "idempotency_key"}),
            idempotency_key=key,
            result=(AdapterResultStatus.SUCCESS if result.status == "success" else AdapterResultStatus.FAILURE),
            failure_reason=result.failure_reason,
            elapsed_ms=elapsed_ms,
        )
        return result

    def generate_site_preview(self, **kwargs) -> AdapterResult:
        return self._invoke(AdapterOperationName.GENERATE_SITE_PREVIEW, self.implementation.generate_site_preview, **kwargs)

    def send_prospect_email(self, **kwargs) -> AdapterResult:
        return self._invoke(AdapterOperationName.SEND_PROSPECT_EMAIL, self.implementation.send_prospect_email, **kwargs)

    def send_delivery_email(self, **kwargs) -> AdapterResult:
        return self._invoke(AdapterOperationName.SEND_DELIVERY_EMAIL, self.implementation.send_delivery_email, **kwargs)

    def create_invoice(self, **kwargs) -> AdapterResult:
        return self._invoke(AdapterOperationName.CREATE_INVOICE, self.implementation.create_invoice, **kwargs)

    def log_outbound_call(self, **kwargs) -> AdapterResult:
        return self._invoke(AdapterOperationName.LOG_OUTBOUND_CALL, self.implementation.log_outbound_call, **kwargs)


def get_pipeline_adapter() -> PipelineAdapter:
    implementation: PipelineAdapter
    if settings.PIPELINE_ADAPTER_MODE == "live":
        implementation = LivePipelineAdapter()
    else:
        implementation = StubPipelineAdapter()
    return TimeoutEnforcingAdapter(implementation)
