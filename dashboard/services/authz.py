from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from dashboard.models import Deal, Lead, Operator, PipelineState, SiteReviewState
from dashboard.services.errors import AuthorizationRejected


class Action(StrEnum):
    OUTREACH_SEND = "outreach.send"
    INVOICE_CREATE = "invoice.create"
    PAYMENT_VERIFY = "payment.verify"
    RELEASE_AUTHORIZE = "release.authorize"
    LEAD_FIELD_EDIT = "lead.edit"
    SITE_APPROVE = "site.approve"
    SITE_REJECT = "site.reject"
    PRICE_SET = "price.set"
    OPERATOR_MANAGE = "operator.manage"
    VARIANT_CONFIGURE = "variant.configure"
    AUDIT_SEARCH = "audit.search"


ROLE_RANK = {
    Operator.Role.VIEWER: 0,
    Operator.Role.AGENT: 1,
    Operator.Role.ADMIN: 2,
}

MIN_ROLE: dict[Action, str] = {
    Action.OUTREACH_SEND: Operator.Role.AGENT,
    Action.INVOICE_CREATE: Operator.Role.AGENT,
    Action.PAYMENT_VERIFY: Operator.Role.AGENT,
    Action.RELEASE_AUTHORIZE: Operator.Role.AGENT,
    Action.LEAD_FIELD_EDIT: Operator.Role.AGENT,
    Action.SITE_APPROVE: Operator.Role.AGENT,
    Action.SITE_REJECT: Operator.Role.AGENT,
    Action.PRICE_SET: Operator.Role.AGENT,
    Action.OPERATOR_MANAGE: Operator.Role.ADMIN,
    Action.VARIANT_CONFIGURE: Operator.Role.ADMIN,
    Action.AUDIT_SEARCH: Operator.Role.ADMIN,
}


class UnmetPrecondition(StrEnum):
    CURRENT_PIPELINE_STATE = "current Pipeline_State"
    MISSING_AGREED_PRICE = "missing agreed_price"
    PAYMENT_VERIFICATION_OUTSTANDING = "payment verification outstanding"
    SITE_NOT_APPROVED = "Site_Project is not Approved"
    COMPLIANCE_BLOCK = "Compliance_Guard blocking condition"
    INSUFFICIENT_ROLE = "insufficient Operator role"


@dataclass(frozen=True)
class Availability:
    permitted: bool
    enabled: bool
    unmet: tuple[UnmetPrecondition, ...] = ()


class Authz:
    @staticmethod
    def permits(operator: Operator, action: Action) -> bool:
        return ROLE_RANK.get(operator.role, -1) >= ROLE_RANK[MIN_ROLE[action]]

    @classmethod
    def check(cls, operator: Operator, action: Action) -> None:
        if cls.permits(operator, action):
            return
        required = MIN_ROLE[action]
        raise AuthorizationRejected(
            f"{action.value} requires the {required} role.",
            target_type="operator",
            target_id=int(operator.pk or 0),
            before_snapshot={"role": operator.role, "required_role": required},
        )


def _latest_site(lead: Lead):
    return lead.site_projects.order_by("-created_at", "-id").first()


def available_actions(lead: Lead, operator: Operator) -> dict[Action, Availability]:
    # Reverse one-to-one access raises RelatedObjectDoesNotExist when a Lead has
    # no Deal; an explicit query keeps "no Deal yet" a normal pipeline state.
    deal = Deal.objects.filter(lead_id=lead.id).first()
    latest_site = _latest_site(lead)
    result: dict[Action, Availability] = {}

    for action in Action:
        unmet: list[UnmetPrecondition] = []
        permitted = Authz.permits(operator, action)
        if not permitted:
            unmet.append(UnmetPrecondition.INSUFFICIENT_ROLE)

        if action == Action.OUTREACH_SEND:
            if not lead.contact_email or lead.unsubscribed_at or lead.manual_review_flag:
                unmet.append(UnmetPrecondition.COMPLIANCE_BLOCK)
        elif action == Action.INVOICE_CREATE:
            if lead.status != PipelineState.WON:
                unmet.append(UnmetPrecondition.CURRENT_PIPELINE_STATE)
            if deal is None or deal.agreed_price is None:
                unmet.append(UnmetPrecondition.MISSING_AGREED_PRICE)
        elif action == Action.PAYMENT_VERIFY:
            if lead.status != PipelineState.PAID_PENDING_VERIFICATION:
                unmet.append(UnmetPrecondition.CURRENT_PIPELINE_STATE)
            if deal is None or deal.payment_anomaly_flag:
                unmet.append(UnmetPrecondition.PAYMENT_VERIFICATION_OUTSTANDING)
        elif action == Action.RELEASE_AUTHORIZE:
            if deal is None or deal.payment_verified_at is None:
                unmet.append(UnmetPrecondition.PAYMENT_VERIFICATION_OUTSTANDING)
            if lead.status != PipelineState.PAYMENT_VERIFIED:
                unmet.append(UnmetPrecondition.CURRENT_PIPELINE_STATE)
        elif action in (Action.SITE_APPROVE, Action.SITE_REJECT):
            if latest_site is None or latest_site.review_state != SiteReviewState.READY_FOR_REVIEW:
                unmet.append(UnmetPrecondition.SITE_NOT_APPROVED)

        result[action] = Availability(
            permitted=permitted,
            enabled=permitted and not unmet,
            unmet=tuple(dict.fromkeys(unmet)),
        )

    return result
