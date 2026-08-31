from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from dashboard.services.outreach_templates import professional_signature


class ReplyCategory(StrEnum):
    INTERESTED = "interested"
    MEETING_REQUEST = "meeting_request"
    QUESTION = "question"
    OBJECTION = "objection"
    NOT_INTERESTED = "not_interested"
    UNSUBSCRIBE = "unsubscribe"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CloserDecision:
    category: ReplyCategory
    confidence: float
    stop_followups: bool
    escalate_to_owner: bool
    auto_send_allowed: bool
    draft_reply: str
    reason: str
    lead_id: str = ""
    thread_id: str = ""
    suppression_required: bool = False


class Closer:
    """Employee #7: classify replies and own the post-win invoice-link step.

    The Closer never auto-sends substantive sales replies. It carries lead/thread
    identifiers forward so the persistence layer can suppress follow-ups on the
    exact prospect rather than treating stop_followups as an in-memory suggestion.

    Once a Deal is already won and the operator approves the invoice action, the
    Closer may ask Stripe to create/finalize the invoice and return the Hosted
    Invoice Page URL. It never emails that URL; customer delivery belongs to
    Sales Bot #5.
    """

    name = "Closer"

    _UNSUBSCRIBE = (
        "unsubscribe",
        "remove me",
        "take me off",
        "stop emailing",
        "do not email",
        "don't email",
    )
    _NOT_INTERESTED = (
        "not interested",
        "no thanks",
        "no thank you",
        "not a fit",
        "pass for now",
    )
    _MEETING = (
        "book a call",
        "schedule a call",
        "set up a call",
        "meet tomorrow",
        "meet next week",
        "what time works",
        "send your calendar",
        "calendar link",
    )
    _INTERESTED = (
        "interested",
        "tell me more",
        "sounds good",
        "let's do it",
        "lets do it",
        "i'm interested",
        "im interested",
    )
    _OBJECTION = (
        "too expensive",
        "already have",
        "we use",
        "not in the budget",
        "budget",
        "price is high",
        "cost is high",
    )

    def generate_invoice_link(
        self,
        *,
        client,
        local_invoice_id: int,
        recipient_email: str,
        customer_name: str,
        amount_usd: int,
        description: str,
    ):
        """Generate the secure Stripe invoice link without sending an email."""
        destination = (recipient_email or "").strip().lower()
        if not destination or "@" not in destination:
            raise ValueError("Closer requires a valid invoice recipient email.")
        if isinstance(amount_usd, bool) or not isinstance(amount_usd, int) or amount_usd <= 0:
            raise ValueError("Closer requires a positive whole-dollar invoice amount.")
        receipt = client.create_invoice_link(
            local_invoice_id=local_invoice_id,
            recipient_email=destination,
            customer_name=(customer_name or destination).strip(),
            amount_usd=amount_usd,
            description=description,
        )
        if not getattr(receipt, "provider_invoice_id", ""):
            raise ValueError("Stripe invoice generation returned no provider invoice ID.")
        if not getattr(receipt, "hosted_invoice_url", ""):
            raise ValueError("Stripe invoice generation returned no hosted invoice URL.")
        return receipt

    def classify(self, reply_text: str) -> tuple[ReplyCategory, float]:
        text = " ".join((reply_text or "").lower().split())
        if any(phrase in text for phrase in self._UNSUBSCRIBE):
            return ReplyCategory.UNSUBSCRIBE, 0.99
        if any(phrase in text for phrase in self._NOT_INTERESTED):
            return ReplyCategory.NOT_INTERESTED, 0.97
        if any(phrase in text for phrase in self._MEETING):
            return ReplyCategory.MEETING_REQUEST, 0.94
        if any(phrase in text for phrase in self._INTERESTED):
            return ReplyCategory.INTERESTED, 0.92
        if any(phrase in text for phrase in self._OBJECTION):
            return ReplyCategory.OBJECTION, 0.86
        if "?" in text:
            return ReplyCategory.QUESTION, 0.78
        return ReplyCategory.UNKNOWN, 0.35

    def run(
        self,
        reply_text: str,
        first_name: str = "there",
        *,
        lead_id: str = "",
        thread_id: str = "",
    ) -> CloserDecision:
        category, confidence = self.classify(reply_text)
        name = (first_name or "there").strip().split()[0]
        signature = professional_signature()
        context = {"lead_id": lead_id, "thread_id": thread_id}

        if category is ReplyCategory.UNSUBSCRIBE:
            return CloserDecision(
                category=category,
                confidence=confidence,
                stop_followups=True,
                escalate_to_owner=False,
                auto_send_allowed=False,
                draft_reply="",
                reason="Explicit opt-out: persist suppression and stop all future outreach.",
                suppression_required=True,
                **context,
            )

        if category is ReplyCategory.NOT_INTERESTED:
            return CloserDecision(
                category=category,
                confidence=confidence,
                stop_followups=True,
                escalate_to_owner=False,
                auto_send_allowed=False,
                draft_reply="",
                reason="Negative reply: close the sequence and suppress automated follow-ups.",
                suppression_required=True,
                **context,
            )

        if category is ReplyCategory.MEETING_REQUEST:
            draft = (
                f"Hi {name},\n\nAbsolutely — happy to set up a quick call. "
                "I can send over available times once scheduling is connected.\n\n"
                f"{signature}"
            )
            return CloserDecision(
                category=category,
                confidence=confidence,
                stop_followups=True,
                escalate_to_owner=True,
                auto_send_allowed=False,
                draft_reply=draft,
                reason="High-intent prospect. Escalate until calendar booking is connected.",
                **context,
            )

        if category is ReplyCategory.INTERESTED:
            draft = (
                f"Hi {name},\n\nThanks for getting back to me. I’d be happy to share more "
                "and see whether this is a fit for your business. What would be most useful for you to know first?\n\n"
                f"{signature}"
            )
            return CloserDecision(
                category=category,
                confidence=confidence,
                stop_followups=True,
                escalate_to_owner=True,
                auto_send_allowed=False,
                draft_reply=draft,
                reason="Positive buying signal. Pause automation and surface the exact thread to the owner.",
                **context,
            )

        if category is ReplyCategory.QUESTION:
            draft = (
                f"Hi {name},\n\nThanks for the question. I want to make sure I give you an accurate answer, "
                "so I’m checking the details before replying.\n\n"
                f"{signature}"
            )
            return CloserDecision(
                category=category,
                confidence=confidence,
                stop_followups=True,
                escalate_to_owner=True,
                auto_send_allowed=False,
                draft_reply=draft,
                reason="Question requires grounded account/service context before a substantive reply.",
                **context,
            )

        if category is ReplyCategory.OBJECTION:
            draft = (
                f"Hi {name},\n\nThat makes sense. I don’t want to push something that isn’t useful. "
                "If you’re open to it, I can clarify the scope and see whether there’s a simpler fit.\n\n"
                f"{signature}"
            )
            return CloserDecision(
                category=category,
                confidence=confidence,
                stop_followups=True,
                escalate_to_owner=True,
                auto_send_allowed=False,
                draft_reply=draft,
                reason="Objection needs owner-approved pricing/scope context before negotiation.",
                **context,
            )

        return CloserDecision(
            category=ReplyCategory.UNKNOWN,
            confidence=confidence,
            stop_followups=True,
            escalate_to_owner=True,
            auto_send_allowed=False,
            draft_reply="",
            reason="Ambiguous reply: do not guess. Pause outreach and request owner review.",
            **context,
        )
