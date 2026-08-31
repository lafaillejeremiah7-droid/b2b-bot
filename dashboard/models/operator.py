"""The ``operators`` table — the custom user model (Requirements 1.5, 9.5, 9.6).

This is the only model defined at task 1.3, and it is defined *here*, in the
scaffolding task, for one reason: ``AUTH_USER_MODEL`` is a swappable dependency
and Django resolves it when the first migration that defines a model is built.
Task 1.1's single migration creates the ``pg_trgm`` extension and attaches no
model state, so the swappable dependency is still unconstrained. Deferring the
Operator to the auth task (task 4) would mean rebuilding a schema that already
had tables in it.

Scope boundaries, stated so the next tasks do not find their work half-done:

* The role *ordering* ``Viewer < Agent < Admin`` is declared here (§3.2) so an
  ordered comparison is expressible. The ``MIN_ROLE`` table, ``Action``,
  ``available_actions()`` and ``Authz.check`` are **task 4.2's** and are not
  here.
* Requirement 9.12 rejects enabling Slack delivery with no webhook target
  recorded. That validation is **task 16.1's**. This module only makes the
  question answerable, by modelling the webhook so that "recorded" is exactly
  "``slack_webhook_url IS NOT NULL``" — see the constraint below.
* ``login_attempts`` (§3.1, append-only, hashed identifiers) is **task 2.3's**
  table and ``AuthService`` is **task 4.1's**. Neither appears here.
"""

from __future__ import annotations

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower, Now, Trim
from django.utils import timezone


class Role(models.TextChoices):
    """The three Operator roles of Requirement 1.5, in ascending authority.

    Declaration order *is* the authority order (§3.2: ``Viewer < Agent <
    Admin``), and :attr:`rank` derives from it rather than restating it, so the
    ordering cannot drift from the enum. The stored values are the requirement's
    own spellings, which keeps the database ``CHECK`` readable against the
    requirement text and matches how §4.3 stores every other closed value set
    (``leads.status``, ``site_projects.review_state``) as its literal name.
    """

    VIEWER = "Viewer", "Viewer"
    AGENT = "Agent", "Agent"
    ADMIN = "Admin", "Admin"

    @property
    def rank(self) -> int:
        """Position in the ``Viewer < Agent < Admin`` order, lowest first."""
        return list(Role).index(self)

    def at_least(self, minimum: "Role") -> bool:
        """Whether this role carries at least ``minimum``'s authority.

        The single ordered comparison task 4.2 needs to evaluate its
        ``MIN_ROLE`` table against an Operator's role. It answers only "is this
        role high enough"; which role an action *requires* is task 4.2's table,
        deliberately not this module's business.
        """
        return self.rank >= Role(minimum).rank


class OperatorManager(BaseUserManager):
    """Creates Operator accounts, always at the ``Viewer`` role.

    ``create_operator`` takes no ``role`` argument. That absence is the point:
    Requirement 1.5 says every newly created account is a Viewer, and an
    optional ``role=`` keyword here would be a second, quieter way to mint an
    Agent or an Admin at creation time. Role is reached only by the Admin-only
    role change of Requirement 1.5, which task 4.2 owns.
    """

    def create_operator(
        self,
        email: str,
        password: str | None = None,
        **extra_fields: object,
    ) -> "Operator":
        """Create one Operator with a usable password and the Viewer role."""
        if not email or not email.strip():
            raise ValueError("An Operator requires a registered email address.")
        extra_fields.pop("role", None)
        operator = self.model(email=self.normalize_identifier(email), **extra_fields)
        operator.set_password(password)
        operator.save(using=self._db)
        return operator

    # Django's own helpers and test utilities call `create_user`; keep one
    # implementation rather than two that can disagree.
    create_user = create_operator

    @classmethod
    def normalize_identifier(cls, email: str) -> str:
        """Fold a submitted identifier to its stored form: trimmed, lowercased.

        Sign-in (task 4.1) looks an Operator up by this value, so normalizing on
        the way in is what makes the identifier case-insensitive while
        ``unique=True`` stays meaningful. The database holds the same rule as a
        ``CHECK`` so no other writer can store an unnormalized address.
        """
        return email.strip().lower()

    def get_by_natural_key(self, username: str | None) -> "Operator":
        if username is None:
            raise self.model.DoesNotExist("No identifier submitted.")
        return self.get(**{self.model.USERNAME_FIELD: self.normalize_identifier(username)})


class Operator(AbstractBaseUser):
    """An authenticated human user of the dashboard.

    **Base class: a lean ``AbstractBaseUser`` with a custom manager, and
    deliberately no ``PermissionsMixin``.** ``PermissionsMixin`` would add
    ``is_superuser``, ``groups`` and ``user_permissions`` — a second,
    independent authority over what an Operator may do. Design §3.2 is built on
    the opposite: authorization is one table (``MIN_ROLE``) consulted through
    one function (``available_actions``), so that "which controls the UI
    rendered" and "which actions the service layer applies" cannot drift
    (Requirement 1.10). A Django permission granted through a group would be
    invisible to that function while still reading as authority to anything
    calling ``has_perm``, which is precisely the drift the design removes. The
    usual reason to accept that cost is ``django.contrib.admin``, which checks
    ``is_staff`` and per-model permissions — and admin is deliberately not
    installed. So the role field is the whole authorization model, and
    ``AbstractBaseUser`` (password hashing, ``last_login``, session auth) is
    exactly the part of ``django.contrib.auth`` this design uses.
    """

    # Explicit BigAutoField rather than inherited from DEFAULT_AUTO_FIELD: the
    # design references this key as `bigint` from four tables
    # (`deals.verified_by_operator_id`, `release_authorizations.operator_id`,
    # `audit_entries.actor_id`, `pipeline_state_history.actor_id` — §4.1), and a
    # later change to that setting must not be able to narrow it to `integer`
    # and silently mismatch every one of those references.
    id = models.BigAutoField(primary_key=True)

    email = models.EmailField(
        max_length=320,
        unique=True,
        verbose_name="registered email address",
        help_text=(
            "The sign-in identifier (Requirement 1.2) and the address the email "
            "notification channel delivers to (Requirement 9.6). Stored trimmed "
            "and lowercased."
        ),
    )

    role = models.CharField(
        max_length=16,
        choices=Role.choices,
        default=Role.VIEWER,
        db_default=Role.VIEWER,
        help_text="Requirement 1.5: exactly one of Viewer, Agent, or Admin.",
    )

    slack_webhook_url = models.URLField(
        max_length=2048,
        null=True,
        blank=True,
        verbose_name="Slack webhook target",
        help_text=(
            "Requirement 9.5: optional. NULL means no Slack target is recorded, "
            "which is the condition Requirement 9.12 (task 16.1) tests before "
            "letting Slack delivery be enabled."
        ),
    )

    is_active = models.BooleanField(
        default=True,
        db_default=True,
        help_text=(
            "A deactivated account cannot establish a session. Account "
            "management is the Admin-only action of Requirement 1.9."
        ),
    )

    created_at = models.DateTimeField(
        default=timezone.now,
        db_default=Now(),
        editable=False,
        help_text="UTC, per Requirement 13.11.",
    )

    objects = OperatorManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "operators"
        verbose_name = "operator"
        verbose_name_plural = "operators"
        constraints = [
            # Requirement 1.5 in the database, not only in Python. `choices` is
            # a form/validation-time hint that a raw INSERT, a data migration,
            # or the future bot's connection never sees; §4.3's rule is that a
            # closed value set is declared where every writer meets it. A fourth
            # role is therefore unstorable, not merely unvalidated.
            models.CheckConstraint(
                condition=models.Q(role__in=Role.values),
                name="operators_role_in_enum",
                violation_error_message=(
                    "role must be exactly one of Viewer, Agent, or Admin "
                    "(Requirement 1.5)."
                ),
            ),
            # An identifier is stored in exactly one form, so `unique` on it is
            # genuinely one account per address: without this, 'A@x.com' and
            # 'a@x.com' are two accounts and sign-in becomes case-dependent.
            models.CheckConstraint(
                condition=models.Q(email=Lower(Trim("email"))),
                name="operators_email_normalized",
                violation_error_message=(
                    "the registered email address must be stored trimmed and "
                    "lowercased."
                ),
            ),
            models.CheckConstraint(
                condition=models.Q(email__gt=""),
                name="operators_email_present",
                violation_error_message=(
                    "a registered email address is required (Requirement 9.6)."
                ),
            ),
            # "Recorded" has to mean one thing for Requirement 9.12 to be a
            # decidable test. With both NULL and '' storable, an Operator who
            # cleared the field would have a target that is absent by intent and
            # present by predicate. NULL is the only representation of absent.
            models.CheckConstraint(
                condition=models.Q(slack_webhook_url__isnull=True)
                | models.Q(slack_webhook_url__gt=""),
                name="operators_slack_webhook_null_or_present",
                violation_error_message=(
                    "the Slack webhook target is either unset or a non-empty "
                    "target; the empty string is not a recorded target "
                    "(Requirement 9.5)."
                ),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.email} ({self.role})"

    @property
    def role_enum(self) -> Role:
        """The stored role as its enum member, for ordered comparison."""
        return Role(self.role)

    def has_role_at_least(self, minimum: Role) -> bool:
        """Whether this Operator's role carries at least ``minimum``'s authority.

        The ordered comparison of §3.2 exposed on the instance. Task 4.2 builds
        the ``MIN_ROLE`` table and ``Authz.check`` on top of this; nothing here
        decides what any action requires.
        """
        return self.role_enum.at_least(minimum)

    @property
    def has_slack_target(self) -> bool:
        """Whether a Slack webhook target is recorded (Requirements 9.5, 9.12)."""
        return self.slack_webhook_url is not None
