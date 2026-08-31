# B2B Bot

A Python/Django B2B automation company built around eight cooperating bot employees.

1. Scout — discovers candidate businesses and produces digest-bound discovery evidence.
2. Researcher — verifies public business, website, and contact evidence.
3. Qualifier — deterministically approves or rejects leads using verified evidence.
4. Personalizer — prepares grounded outreach without inventing website criticisms.
5. Sales Bot — controls outbound delivery, revalidates clearance, and uses the configured Yahoo business SMTP boundary.
6. Manager — enforces pipeline handoffs, reports the true blocker, and stops unsafe downstream work.
7. Closer — classifies replies, persists suppression, and generates/finalizes approved Stripe invoice links.
8. Boss — supervises company outcomes and priorities without bypassing worker controls or human approval gates.

## Runtime safety

External side effects fail closed. Stub mode never reports a customer email or delivery as completed. Live outbound email uses Yahoo SMTP credentials supplied only through private environment variables. Stripe creates hosted invoice links after approval; Stripe itself does not send the customer email. Payment webhooks and generic pipeline events are authenticated, and release retries stay bound to the originally approved recipient, archive URL, and idempotency identity.

The dashboard visual redesign is separate from runtime hardening; the planned UI is an animated multi-room company view with selectable rooms and visible employees working together.
