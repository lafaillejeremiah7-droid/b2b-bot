# B2B Bot

A Python/Django B2B automation company built around seven specialized bot employees.

1. Scout — discovers candidate businesses.
2. Researcher — gathers business/contact context.
3. Qualifier — approves or rejects leads.
4. Personalizer — prepares grounded outreach.
5. Sales Bot — controls outbound submission.
6. Manager — enforces pipeline gates and records outcomes.
7. Closer — handles inbound replies, stops follow-ups on opt-outs/negative replies, drafts responses for positive leads, and escalates questions/objections that need owner context.

## Current safety mode

The runtime foundation is intentionally conservative while the database, compliance, and Gmail adapters are being completed. External prospect sends remain blocked by the minimal outbound pipeline; internal Gmail self-tests are permitted. The Closer does not auto-send substantive sales or negotiation replies yet.
