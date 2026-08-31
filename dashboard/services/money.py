from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse
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


def _validated_email(value: str) -> str:
    email = (value or "").strip().lower()
    if not email or "@" not in email or len(email) > 320:
        raise ValidationRejected("The Lead has no valid contact email for delivery.")
    local, _, domain = email.partition("@")
    if not local or not domain or "." not in domain:
        raise ValidationRejected("The Lead has no valid contact email for delivery.")
    return email


def _validated_archive_link(value: str) -> str:
    link = (value or "").strip()
    parsed = urlparse(link)
    if parsed.scheme != "https" or not parsed.netloc or len(link) > 2048:
        raise ValidationRejected("Delivery archive link must be a valid HTTPS URL no longer than 2048 characters.")
    return link


def _release_audit(authorization: ReleaseAuthorization):
    return authorization.operator.audit_entries.filter(
        action_type=AuditActionType.RELEASE_AUTHORIZATION,
        target_type="releaseauthorization",
        target_id=authorization.id,
    ).order_by("-occurred_at", "-id").first()


def _release_snapshot(authorization: ReleaseAuthorization):
    audit = _release_audit(authorization)
    after = audit.after_value if audit is not None and isinstance(audit.after_value, dict) else {}
    recipient = str(after.get("recipient_email") or "").strip().lower()
    archive_link = str(after.get("archive_link") or "").strip()
    raw_key = str(after.get("delivery_idempotency_key") or "").strip()
    if not recipient or not archive_link or not raw_key:
        raise ValidationRejected(
            "Release authorization is missing its immutable delivery snapshot and cannot be retried safely."
        )
    try:
        key = UUID(raw_key)
    except (TypeError, ValueError) as exc:
        raise ValidationRejected("Release authorization has an invalid delivery identity.") from exc
    return audit, _validated_email(recipient), _validated_archive_link(archive_link), key


class InvoiceManager:
    @staticmethod
    def create_invoice(*, deal_id: int, operator: Operator, idempotency_key: UUID | None = None) -> InvoiceOutcome:
        Authz.check(operator, Action.INVOICE_CREATE)
        key = idempotency_key or uuid4()

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

        with transaction.atomic():
            deal = Deal.objects.select_for_update().select_related("lead").get(pk=deal_id)
            existing = Invoice.objects.filter(deal_id=deal.pk).first()
            if existing is not None:
                return InvoiceOutcome(existing, result, key)
            if deal.lead.status != PipelineState.WON:
                raise ValidationRejected("Deal state changed while invoice creation was in progress; retry the action.")
            if deal.agreed_price != amount:
                raise ValidationRejected("Agreed price changed while invoice creation was in progress; retry the action.")
            invoice_number = str(result.payload.get("invoice_number") or f"INV-{deal.pk}-{str(key)[:8]}")
            try:
                # Keep the uniqueness race in its own savepoint so a conflict
                # does not poison the outer transaction before the fallback read.
                with transaction.atomic():
                    invoice = Invoice.objects.create(
                        deal=deal,
                        invoice_number=invoice_number[:200],
                        amount=amount,
                    )
            except IntegrityError:
                invoice = Invoice.objects.filter(deal_id=deal.pk).first()
                if invoice is None:
                    raise ValidationRejected("Invoice identity collided with another record; retry the action.")
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
            if (
                payment.deal_id != deal.pk
                or payment.amount_usd != amount_usd
                or payment.paid_date != paid_date
            ):
                raise ValidationRejected("Payment event_id was replayed with different payment facts.")
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
    def _submit_snapshot(
        *,
        authorization: ReleaseAuthorization,
        audit,
        operator: Operator,
        recipient_email: str,
        archive_link: str,
        delivery_key: UUID,
    ) -> ReleaseOutcome:
        result = get_pipeline_adapter().send_delivery_email(
            deal_id=authorization.deal_id,
            to_email=recipient_email,
            archive_link=archive_link,
            idempotency_key=delivery_key,
        )
        if result.status != "success":
            return ReleaseOutcome(authorization, result)

        with transaction.atomic():
            deal = Deal.objects.select_for_update().select_related("lead").get(pk=authorization.deal_id)
            authorization = ReleaseAuthorization.objects.select_related("operator").get(pk=authorization.pk)
            if deal.delivery_sent is True:
                return ReleaseOutcome(authorization, result, already_authorized=True)
            if deal.payment_verified_at is None:
                raise ValidationRejected("Payment verification disappeared before delivery could be recorded.")
            if deal.lead.status != PipelineState.PAYMENT_VERIFIED:
                raise ValidationRejected("Pipeline state changed before delivery could be recorded.")
            delivered_at = timezone.now()
            deal.delivery_sent = True
            deal.delivered_date = delivered_at
            deal.save(update_fields=["delivery_sent", "delivered_date"])
            _transition_with_existing_audit(
                lead=deal.lead,
                target=PipelineState.RELEASED,
                actor=authorization.operator,
                audit_entry=audit,
                occurred_at=delivered_at,
            )
        return ReleaseOutcome(authorization, result)

    @classmethod
    def authorize_release(
        cls,
        *,
        deal_id: int,
        operator: Operator,
        session,
        confirmation_token: str,
        archive_link: str,
        idempotency_key: UUID | None = None,
    ) -> ReleaseOutcome:
        Authz.check(operator, Action.RELEASE_AUTHORIZE)
        requested_archive = _validated_archive_link(archive_link)
        consume_confirmation(session, token=confirmation_token, action="release.authorize", target_id=deal_id)
        requested_key = idempotency_key or uuid4()

        with transaction.atomic():
            deal = Deal.objects.select_for_update().select_related("lead").get(pk=deal_id)
            if deal.payment_verified_at is None:
                raise ValidationRejected("Payment verification outstanding.")
            if deal.lead.status != PipelineState.PAYMENT_VERIFIED:
                if deal.delivery_sent is True and deal.lead.status == PipelineState.RELEASED:
                    authorization = ReleaseAuthorization.objects.get(deal_id=deal.pk)
                    return ReleaseOutcome(authorization, None, already_authorized=True)
                raise ValidationRejected("Release requires Pipeline_State Payment_Verified.")

            existing = (
                ReleaseAuthorization.objects.select_related("operator")
                .filter(deal_id=deal.pk)
                .first()
            )
            if existing is not None:
                if deal.delivery_sent is True:
                    return ReleaseOutcome(existing, None, already_authorized=True)
                audit, recipient_email, authorized_archive, delivery_key = _release_snapshot(existing)
                if requested_archive != authorized_archive:
                    raise ValidationRejected(
                        "A pending release retry must use the exact archive link that was originally authorized."
                    )
                authorization = existing
            else:
                recipient_email = _validated_email(deal.lead.contact_email)
                authorized_archive = requested_archive
                delivery_key = requested_key
                authorized_at = timezone.now()
                try:
                    # Isolate the unique-authority race in a savepoint; otherwise
                    # catching IntegrityError would leave the outer transaction
                    # unusable before the fallback query.
                    with transaction.atomic():
                        authorization = ReleaseAuthorization.objects.create(
                            deal=deal,
                            operator=operator,
                            authorized_at=authorized_at,
                        )
                except IntegrityError:
                    authorization = (
                        ReleaseAuthorization.objects.select_related("operator")
                        .filter(deal_id=deal.pk)
                        .first()
                    )
                    if authorization is None:
                        raise ValidationRejected("Release authorization conflicted with another record; retry.")
                    audit, recipient_email, authorized_archive, delivery_key = _release_snapshot(authorization)
                    if requested_archive != authorized_archive:
                        raise ValidationRejected(
                            "A concurrent release authorization already fixed a different archive link."
                        )
                else:
                    audit = AuditLogger.record(
                        operator,
                        AuditActionType.RELEASE_AUTHORIZATION,
                        authorization,
                        None,
                        {
                            "deal_id": deal.pk,
                            "authorized_at": authorized_at.isoformat(),
                            "recipient_email": recipient_email,
                            "archive_link": authorized_archive,
                            "delivery_idempotency_key": str(delivery_key),
                        },
                        occurred_at=authorized_at,
                    )

        return cls._submit_snapshot(
            authorization=authorization,
            audit=audit,
            operator=operator,
            recipient_email=recipient_email,
            archive_link=authorized_archive,
            delivery_key=delivery_key,
        )

    @classmethod
    def retry_delivery(
        cls,
        *,
        deal_id: int,
        operator: Operator,
        archive_link: str,
        idempotency_key: UUID,
    ) -> ReleaseOutcome:
        """Compatibility retry API locked to the original authorization snapshot."""
        Authz.check(operator, Action.RELEASE_AUTHORIZE)
        requested_archive = _validated_archive_link(archive_link)
        with transaction.atomic():
            deal = Deal.objects.select_for_update().select_related("lead").get(pk=deal_id)
            authorization = ReleaseAuthorization.objects.select_related("operator").get(deal_id=deal.pk)
            if deal.delivery_sent is True:
                return ReleaseOutcome(authorization, None, already_authorized=True)
            if deal.payment_verified_at is None or deal.lead.status != PipelineState.PAYMENT_VERIFIED:
                raise ValidationRejected("Release retry requires an unmodified Payment_Verified deal.")
            audit, recipient_email, authorized_archive, delivery_key = _release_snapshot(authorization)
            if requested_archive != authorized_archive:
                raise ValidationRejected("Release retry cannot change the originally authorized archive link.")
            if idempotency_key != delivery_key:
                raise ValidationRejected("Release retry cannot change the original delivery idempotency key.")

        return cls._submit_snapshot(
            authorization=authorization,
            audit=audit,
            operator=operator,
            recipient_email=recipient_email,
            archive_link=authorized_archive,
            delivery_key=delivery_key,
        )
