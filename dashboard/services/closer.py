from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


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


class Closer:
    """Employee #7: classify inbound replies and decide the safest next action.

    The first production version is deliberately conservative. It never auto-sends
    substantive sales replies. Interested prospects and questions are escalated to
    the owner with a ready-to-review draft. Opt-outs stop follow-ups immediately.
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

    def run(self, reply_text: str, first_name: str = "there") -> CloserDecision:
        category, confidence = self.classify(reply_text)
        name = (first_name or "there").strip().split()[0]

        if category is ReplyCategory.UNSUBSCRIBE:
            return CloserDecision(
                category=category,
                confidence=confidence,
                stop_followups=True,
                escalate_to_owner=False,
                auto_send_allowed=False,
                draft_reply="",
                reason="Explicit opt-out: suppress all future outreach immediately.",
            )

        if category is ReplyCategory.NOT_INTERESTED:
            return CloserDecision(
                category=category,
                confidence=confidence,
                stop_followups=True,
                escalate_to_owner=False,
                auto_send_allowed=False,
                draft_reply="",
                reason="Negative reply: stop the sequence and close the outreach loop.",
            )

        if category is ReplyCategory.MEETING_REQUEST:
            return CloserDecision(
                category=category,
                confidence=confidence,
                stop_followups=True,
                escalate_to_owner=True,
                auto_send_allowed=False,
                draft_reply=(
                    f"Hi {name},\n\nAbsolutely — happy to set up a quick call. "
                    "I can send over available times once scheduling is connected.\n\nBest,\nB2B Bot"
                ),
                reason="High-intent prospect. Escalate until calendar booking is connected.",
            )

        if category is ReplyCategory.INTERESTED:
            return CloserDecision(
                category=category,
                confidence=confidence,
                stop_followups=True,
                escalate_to_owner=True,
                auto_send_allowed=False,
                draft_reply=(
                    f"Hi {name},\n\nThanks for getting back to me. I’d be happy to share more "
                    "and see whether this is a fit for your business. What would be most useful for you to know first?\n\nBest,\nB2B Bot"
                ),
                reason="Positive buying signal. Stop automated follow-ups and surface to the owner.",
            )

        if category is ReplyCategory.QUESTION:
            return CloserDecision(
                category=category,
                confidence=confidence,
                stop_followups=True,
                escalate_to_owner=True,
                auto_send_allowed=False,
                draft_reply=(
                    f"Hi {name},\n\nThanks for the question. I want to make sure I give you an accurate answer, "
                    "so I’m checking the details before replying.\n\nBest,\nB2B Bot"
                ),
                reason="Question requires grounded account/service context before a substantive reply.",
            )

        if category is ReplyCategory.OBJECTION:
            return CloserDecision(
                category=category,
                confidence=confidence,
                stop_followups=True,
                escalate_to_owner=True,
                auto_send_allowed=False,
                draft_reply=(
                    f"Hi {name},\n\nThat makes sense. I don’t want to push something that isn’t useful. "
                    "If you’re open to it, I can clarify the scope and see whether there’s a simpler fit.\n\nBest,\nB2B Bot"
                ),
                reason="Objection needs owner-approved pricing/scope context before negotiation.",
            )

        return CloserDecision(
            category=ReplyCategory.UNKNOWN,
            confidence=confidence,
            stop_followups=True,
            escalate_to_owner=True,
            auto_send_allowed=False,
            draft_reply="",
            reason="Ambiguous reply: do not guess. Pause outreach and request owner review.",
        )
