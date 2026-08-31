from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from dashboard.adapter import AdapterResult, get_pipeline_adapter
from dashboard.models import (
    AuditActionType,
    Deal,
    HistoryActorKind,
    Invoice,
    Lead,
    Operator,
    Payment,
    PipelineState,
    PipelineStateHistory,
    ReleaseAuthorization,
)
from dashboard.services.audit import AuditLogger
from dashboard.services.authz import Action, Authz
from dashboard.services.confirmation import consume_confirmation
from dashboard.services.errors import TransitionRejected, ValidationRejected
from dashboard.services.pipeline_state import LEGAL_TRANSITIONS, PipelineStateMachine


@dataclass(frozen=True)
class InvoiceOutcome:
    invoice: Invoice | None
    adapter_result: AdapterResult
    idempotency_key: UUID


@dataclass(frozen=True)
class PaymentVerificationOutcome:
    deal: Deal
    difference: int


@dataclass(frozen=True)
class ReleaseOutcome:
    authorization: ReleaseAuthorization
    adapter_result: AdapterResult | None
    already_authorized: bool = False


def _transition_with_existing_audit(
    *,
    lead: Lead,
    target: PipelineState,
    actor: Operator,
    audit_entry,
    occurred_at=None,
) -> None:
    """Advance a state while linking history to the action's single audit entry."""
    current = PipelineState(lead.status)
    if (current, target) not in LEGAL_TRANSITIONS:
        raise TransitionRejected(
            f"Illegal transition {current.value} → {target.value}.",
            target_type="lead",
            target_id=lead.id,
            before_snapshot={"status": current.value},
        )
    occurred_at = occurred_at or timezone.now()
    prior_version = lead.state_version
    updated = Lead.objects.filter(
        pk=lead.id,
        status=current.value,
        state_version=prior_version,
    ).update(
        status=target.value,
        state_version=F("state_version") + 1,
        last_activity_at=occurred_at,
    )
    if updated != 1:
        raise TransitionRejected("Lead changed before the action completed.", target_type="lead", target_id=lead.id)
    PipelineStateHistory.objects.create(
        lead=lead,
        from_state=current.value,
        to_state=target.value,
        occurred_at=occurred_at,
        actor=actor,
        actor_kind=HistoryActorKind.OPERATOR,
        audit_entry=audit_entry,
    )
    lead.status = target.value
    lead.state_version = prior_version + 1
    lead.last_activity_at = occurred_at


class InvoiceManager:
    @staticmethod
    def create_invoice(*, deal_id: int, operator: Operator, idempotency_key: UUID | None = None) -> InvoiceOutcome:
        Authz.check(operator, Action.INVOICE_CREATE)
        key = idempotency_key or uuid4()

        # Phase 1: validate and snapshot under lock. No external call in a txn.
        with transaction.atomic():
            deal = Deal.objects.select_for_update().select_related("lead").get(pk=deal_id)
            if deal.lead.status != PipelineState.WON:
                raise ValidationRejected("Invoice creation requires Pipeline_State Won.")
            if deal.agreed_price is None:
                raise ValidationRejected("Set agreed_price before creating an invoice.")
            existing = Invoice.objects.filter(deal_id=deal.pk).first()
            if existing is not None:
                return InvoiceOutcome(existing, AdapterResult("success", payload={"existing": True}), key)
            amount = deal.agreed_price

        result = get_pipeline_adapter().create_invoice(
            deal_id=deal_id,
            amount_usd=amount,
            idempotency_key=key,
        )
        if result.status != "success":
            return InvoiceOutcome(None, result, key)

        # Phase 3: persist only after provider success. Uniqueness collapses races.
        with transaction.atomic():
            deal = Deal.objects.select_for_update().select_related("lead").get(pk=deal_id)
            existing = Invoice.objects.filter(deal_id=deal.pk).first()
            if existing is not None:
                return InvoiceOutcome(existing, result, key)
            invoice_number = str(result.payload.get("invoice_number") or f"INV-{deal.pk}-{str(key)[:8]}")
            try:
                invoice = Invoice.objects.create(
                    deal=deal,
                    invoice_number=invoice_number[:200],
                    amount=deal.agreed_price,
                )
            except IntegrityError:
                invoice = Invoice.objects.get(deal_id=deal.pk)
                return InvoiceOutcome(invoice, result, key)
            deal.invoice_id = invoice.id
            deal.save(update_fields=["invoice_id"])
            audit = AuditLogger.record(
                operator,
                AuditActionType.INVOICE_CREATION,
                invoice,
                None,
                {"invoice_number": invoice.invoice_number, "amount": invoice.amount},
            )
            _transition_with_existing_audit(
                lead=deal.lead,
                target=PipelineState.INVOICED,
                actor=operator,
                audit_entry=audit,
            )
            return InvoiceOutcome(invoice, result, key)


class PaymentService:
    @staticmethod
    @transaction.atomic
    def record_received(
        *,
        deal: Deal,
        event_id: str,
        amount_usd: int,
        paid_date,
    ) -> tuple[Payment, str | None]:
        """Record money atomically; a state anomaly never erases a real payment."""
        if isinstance(amount_usd, bool) or not isinstance(amount_usd, int) or not 1 <= amount_usd <= 1000:
            raise ValidationRejected("Payment amount must be a whole dollar value from 1 to 1000.")
        deal = Deal.objects.select_for_update().select_related("lead").get(pk=deal.pk)
        payment, created = Payment.objects.get_or_create(
            event_id=event_id,
            defaults={"deal": deal, "amount_usd": amount_usd, "paid_date": paid_date},
        )
        if not created:
            return payment, None

        deal.payment_received = True
        deal.paid_date = paid_date
        deal.save(update_fields=["payment_received", "paid_date"])

        if not Invoice.objects.filter(deal_id=deal.pk).exists():
            reason = "payment received without an invoice record"
            deal.payment_anomaly_flag = True
            deal.payment_anomaly_reason = reason
            deal.save(update_fields=["payment_anomaly_flag", "payment_anomaly_reason"])
            return payment, reason

        try:
            with transaction.atomic():
                PipelineStateMachine.request_from_event(
                    lead_id=deal.lead_id,
                    event_type="payment_received",
                    event_id=event_id,
                )
        except TransitionRejected:
            deal.lead.refresh_from_db(fields=["status"])
            reason = f"Pipeline_State {deal.lead.status} cannot transition to Paid_Pending_Verification"
            deal.payment_anomaly_flag = True
            deal.payment_anomaly_reason = reason[:500]
            deal.save(update_fields=["payment_anomaly_flag", "payment_anomaly_reason"])
            return payment, reason
        return payment, None

    @staticmethod
    @transaction.atomic
    def clear_anomaly(*, deal_id: int, operator: Operator) -> Deal:
        Authz.check(operator, Action.PAYMENT_VERIFY)
        deal = Deal.objects.select_for_update().get(pk=deal_id)
        if not deal.payment_anomaly_flag:
            raise ValidationRejected("This Deal has no payment anomaly to clear.")
        reason = deal.payment_anomaly_reason
        deal.payment_anomaly_flag = False
        deal.payment_anomaly_reason = None
        deal.save(update_fields=["payment_anomaly_flag", "payment_anomaly_reason"])
        AuditLogger.record(
            operator,
            AuditActionType.PAYMENT_ANOMALY_CLEARING,
            deal,
            {"payment_anomaly_flag": True, "reason": reason},
            {"payment_anomaly_flag": False, "reason": None},
        )
        return deal


class PaymentVerifier:
    @staticmethod
    @transaction.atomic
    def verify(
        *,
        deal_id: int,
        operator: Operator,
        session=None,
        mismatch_confirmation_token: str | None = None,
    ) -> PaymentVerificationOutcome:
        Authz.check(operator, Action.PAYMENT_VERIFY)
        deal = Deal.objects.select_for_update().select_related("lead").get(pk=deal_id)
        if deal.lead.status != PipelineState.PAID_PENDING_VERIFICATION:
            raise ValidationRejected("Payment verification requires Paid_Pending_Verification.")
        if deal.payment_verified_at is not None:
            raise ValidationRejected("Payment verification is already recorded.")
        if deal.payment_anomaly_flag:
            raise ValidationRejected("Clear the payment anomaly before verification.")
        invoice = Invoice.objects.filter(deal_id=deal.pk).first()
        payment = Payment.objects.filter(deal_id=deal.pk).order_by("-paid_date", "-id").first()
        if invoice is None or payment is None:
            raise ValidationRejected("Invoice and payment records are required for verification.")
        difference = payment.amount_usd - invoice.amount
        if difference != 0:
            if session is None or not mismatch_confirmation_token:
                raise ValidationRejected(
                    f"Payment differs from invoice by ${abs(difference)}; a second confirmation is required."
                )
            consume_confirmation(
                session,
                token=mismatch_confirmation_token,
                action="payment.verify.amount_mismatch",
                target_id=deal.pk,
            )

        verified_at = timezone.now()
        deal.payment_verified_at = verified_at
        deal.verified_by_operator = operator
        deal.save(update_fields=["payment_verified_at", "verified_by_operator"])
        audit = AuditLogger.record(
            operator,
            AuditActionType.PAYMENT_VERIFICATION,
            deal,
            {"payment_verified_at": None, "payment_amount": payment.amount_usd, "invoice_amount": invoice.amount},
            {"payment_verified_at": verified_at.isoformat(), "difference": difference},
            occurred_at=verified_at,
        )
        _transition_with_existing_audit(
            lead=deal.lead,
            target=PipelineState.PAYMENT_VERIFIED,
            actor=operator,
            audit_entry=audit,
            occurred_at=verified_at,
        )
        return PaymentVerificationOutcome(deal, difference)


class ReleaseGate:
    @staticmethod
    def authorize_release(
        *,
        deal_id: int,
        operator: Operator,
        session,
        confirmation_token: str,
        archive_link: str,
        idempotency_key: UUID | None = None,
    ) -> ReleaseOutcome:
        Authz.check(operator, Action.RELEASE_AUTHORIZE)
        consume_confirmation(session, token=confirmation_token, action="release.authorize", target_id=deal_id)
        key = idempotency_key or uuid4()

        with transaction.atomic():
            deal = Deal.objects.select_for_update().select_related("lead").get(pk=deal_id)
            # Requirement 8.9: verification is checked before state.
            if deal.payment_verified_at is None:
                raise ValidationRejected("Payment verification outstanding.")
            if deal.lead.status != PipelineState.PAYMENT_VERIFIED:
                raise ValidationRejected("Release requires Pipeline_State Payment_Verified.")
            existing = ReleaseAuthorization.objects.filter(deal_id=deal.pk).first()
            if existing is not None:
                return ReleaseOutcome(existing, None, already_authorized=True)
            authorized_at = timezone.now()
            try:
                authorization = ReleaseAuthorization.objects.create(
                    deal=deal,
                    operator=operator,
                    authorized_at=authorized_at,
                )
            except IntegrityError:
                authorization = ReleaseAuthorization.objects.get(deal_id=deal.pk)
                return ReleaseOutcome(authorization, None, already_authorized=True)
            audit = AuditLogger.record(
                operator,
                AuditActionType.RELEASE_AUTHORIZATION,
                authorization,
                None,
                {"deal_id": deal.pk, "authorized_at": authorized_at.isoformat()},
                occurred_at=authorized_at,
            )
            to_email = deal.lead.contact_email
            if not to_email:
                raise ValidationRejected("The Lead has no contact email for delivery.")

        result = get_pipeline_adapter().send_delivery_email(
            deal_id=deal_id,
            to_email=to_email,
            archive_link=archive_link,
            idempotency_key=key,
        )
        if result.status != "success":
            return ReleaseOutcome(authorization, result)

        with transaction.atomic():
            deal = Deal.objects.select_for_update().select_related("lead").get(pk=deal_id)
            delivered_at = timezone.now()
            deal.delivery_sent = True
            deal.delivered_date = delivered_at
            deal.save(update_fields=["delivery_sent", "delivered_date"])
            # Reuse the release-authorization audit row: one human action, one audit.
            _transition_with_existing_audit(
                lead=deal.lead,
                target=PipelineState.RELEASED,
                actor=operator,
                audit_entry=audit,
                occurred_at=delivered_at,
            )
        return ReleaseOutcome(authorization, result)

    @staticmethod
    def retry_delivery(
        *,
        deal_id: int,
        operator: Operator,
        archive_link: str,
        idempotency_key: UUID,
    ) -> ReleaseOutcome:
        Authz.check(operator, Action.RELEASE_AUTHORIZE)
        with transaction.atomic():
            deal = Deal.objects.select_for_update().select_related("lead").get(pk=deal_id)
            authorization = ReleaseAuthorization.objects.get(deal_id=deal.pk)
            if deal.delivery_sent is True:
                return ReleaseOutcome(authorization, None, already_authorized=True)
            if not deal.lead.contact_email:
                raise ValidationRejected("The Lead has no contact email for delivery.")
            audit = authorization.operator.audit_entries.filter(
                action_type=AuditActionType.RELEASE_AUTHORIZATION,
                target_type="releaseauthorization",
                target_id=authorization.id,
            ).order_by("-occurred_at", "-id").first()

        result = get_pipeline_adapter().send_delivery_email(
            deal_id=deal.pk,
            to_email=deal.lead.contact_email,
            archive_link=archive_link,
            idempotency_key=idempotency_key,
        )
        if result.status == "success":
            with transaction.atomic():
                deal = Deal.objects.select_for_update().select_related("lead").get(pk=deal_id)
                delivered_at = timezone.now()
                deal.delivery_sent = True
                deal.delivered_date = delivered_at
                deal.save(update_fields=["delivery_sent", "delivered_date"])
                if deal.lead.status == PipelineState.PAYMENT_VERIFIED and audit is not None:
                    _transition_with_existing_audit(
                        lead=deal.lead,
                        target=PipelineState.RELEASED,
                        actor=operator,
                        audit_entry=audit,
                        occurred_at=delivered_at,
                    )
        return ReleaseOutcome(authorization, result)
