"""Service layer (design §3.0.1 rules 2, 3 and 4).

Empty by design at task 1.1. Every service entry point added here opens its own
``transaction.atomic()`` block, calls ``Authz.check`` as its first statement, and
is the only kind of callable permitted to reach ``dashboard.adapter``. The
``release_gate`` module, when it lands, is additionally the sole writer of
``release_authorizations`` and the sole caller of ``send_delivery_email``.
"""
