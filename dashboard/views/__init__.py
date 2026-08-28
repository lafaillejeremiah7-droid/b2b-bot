"""View layer (design §3.0.1 rule 1).

Empty by design at task 1.1. Views parse the request, call exactly one
service-layer function, and render the result. They perform no gate evaluation,
no state transitions, and no ORM writes, and hold no reference to
``dashboard.adapter``.
"""
