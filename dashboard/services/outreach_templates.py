from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class OutreachContext:
    business_name: str
    first_name: str = "there"
    source: str = "google_maps"
    website: str = ""
    verified_no_website: bool = False
    observations: tuple[str, ...] = ()
    preview_url: str = ""


def professional_signature() -> str:
    sender = getattr(settings, "OUTREACH_SENDER_NAME", "Jeremiah Lafaille").strip()
    phone = getattr(settings, "OUTREACH_PHONE", "").strip()
    email = getattr(settings, "OUTREACH_EMAIL", "").strip()

    lines = [
        "Best,",
        sender,
        "Website Design & Digital Presence",
    ]
    if phone:
        lines.append(f"Phone Number: {phone}")
    if email:
        lines.append(f"Email: {email}")
    return "\n".join(lines)


def _preview_line(preview_url: str) -> str:
    if not preview_url:
        return "I put together a premium redesign concept specifically for your business so you can see what a stronger online presence could look like."
    return f"I put together a premium redesign concept specifically for your business: {preview_url}"


def render_google_maps_outreach(context: OutreachContext) -> tuple[str, str]:
    """Render the correct outreach path for a verified Google Maps lead.

    Website leads must include at least two verified observations. No-website
    leads must be explicitly verified as having no official website. This
    renderer never guesses either condition.
    """
    if context.source != "google_maps":
        raise ValueError("This renderer is reserved for verified Google Maps leads.")

    first_name = (context.first_name or "there").strip().split()[0]
    business_name = context.business_name.strip()
    signature = professional_signature()

    if context.verified_no_website:
        if context.website:
            raise ValueError("A no-website lead cannot also contain an official website URL.")

        subject = f"website idea for {business_name}"
        body = (
            f"Hi {first_name},\n\n"
            f"I came across {business_name} on Google Maps and noticed you don't currently have a verified dedicated website for the business.\n\n"
            "That means customers who find you through Google, social media, or referrals don't have one central place to quickly see:\n\n"
            "1. What you offer and why they should choose you.\n"
            "2. How to contact, book, request a quote, or take the next step.\n\n"
            f"{_preview_line(context.preview_url)}\n\n"
            "Would you be open to seeing the concept?\n\n"
            f"{signature}"
        )
        return subject, body

    if not context.website:
        raise ValueError("Website leads require an official website URL, or verified_no_website=True.")
    if len(context.observations) < 2:
        raise ValueError("Website outreach requires two verified website observations.")

    observation_one, observation_two = context.observations[:2]
    subject = f"quick idea for {business_name}"
    body = (
        f"Hi {first_name},\n\n"
        f"I came across {business_name} on Google Maps and took a look through your website. I noticed two specific things worth improving:\n\n"
        f"1. {observation_one}\n"
        f"2. {observation_two}\n\n"
        f"{_preview_line(context.preview_url)}\n\n"
        "Would you be open to seeing the concept?\n\n"
        f"{signature}"
    )
    return subject, body
