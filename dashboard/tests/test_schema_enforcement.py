from __future__ import annotations

from django.db import DatabaseError, connection, transaction
from django.test import TestCase
from django.utils import timezone

from dashboard.models import AuditActionType, AuditEntry, Lead, Operator, PipelineState


REQUIRED_LEAD_INDEXES = {
    "idx_leads_status_activity",
    "idx_leads_activity",
    "idx_leads_company",
    "idx_leads_score",
    "idx_leads_email_norm",
    "idx_leads_phone_digits",
    "idx_leads_search_trgm",
}

REQUIRED_GLOBAL_INDEXES = {
    "one_genesis_row_per_lead",
    "idx_history_state_time",
    "idx_history_lead_time",
    "idx_emails_sent",
    "idx_emails_lead_sent",
    "idx_calls_time_outcome",
    "idx_invoices_issued",
    "idx_payments_paid",
    "idx_deals_verified",
    "idx_eva_variant",
    "idx_site_projects_lead_created",
    "idx_audit_time",
    "idx_audit_actor_time",
    "idx_audit_action_time",
    "idx_audit_target",
}

REQUIRED_TRIGGERS = {
    ("audit_entries", "trg_audit_immutable"),
    ("emails", "trg_no_email_after_unsubscribe"),
    ("emails", "trg_no_email_after_bounce"),
    ("calls", "trg_no_call_after_dnc"),
    ("emails", "trg_outreach_channel_match"),
    ("calls", "trg_outreach_channel_match"),
    ("deals", "trg_delivery_guard"),
    ("deals", "trg_agreed_price_frozen"),
    ("deals", "trg_deal_state_consistency"),
    ("leads", "trg_deal_state_consistency"),
    ("emails", "trg_preview_link_approved"),
    ("site_projects", "trg_site_created_at_immutable"),
}


class EnforcementInventoryTests(TestCase):
    def test_design_indexes_are_deployed(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()")
            installed = {name for (name,) in cursor.fetchall()}
        self.assertFalse(REQUIRED_LEAD_INDEXES - installed)
        self.assertFalse(REQUIRED_GLOBAL_INDEXES - installed)

    def test_exact_composite_index_definitions_are_deployed(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexname, indexdef FROM pg_indexes WHERE indexname IN "
                "('idx_eva_variant','idx_audit_time','idx_audit_target')"
            )
            defs = dict(cursor.fetchall())
        self.assertIn("(variant_id, email_id)", defs["idx_eva_variant"])
        self.assertIn("(occurred_at DESC, id DESC)", defs["idx_audit_time"])
        self.assertIn("(target_type, target_id, occurred_at DESC)", defs["idx_audit_target"])

    def test_enforcement_triggers_are_deployed(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.relname, t.tgname
                  FROM pg_trigger t
                  JOIN pg_class c ON c.oid = t.tgrelid
                 WHERE NOT t.tgisinternal
                """
            )
            installed = set(cursor.fetchall())
        self.assertFalse(REQUIRED_TRIGGERS - installed)

    def test_genesis_index_is_partial_and_unique(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexdef FROM pg_indexes WHERE indexname='one_genesis_row_per_lead'"
            )
            definition = cursor.fetchone()[0]
        self.assertIn("UNIQUE INDEX", definition)
        self.assertIn("WHERE (from_state IS NULL)", definition)


class AuditImmutabilityTests(TestCase):
    def setUp(self):
        self.operator = Operator.objects.create_operator("audit@example.com", "pw12345!")
        self.lead = Lead.objects.create(
            company_name="Audit Target",
            researched_score=3,
            status=PipelineState.NEW_LEAD,
            last_activity_at=timezone.now(),
        )
        self.entry = AuditEntry.objects.create(
            actor=self.operator,
            action_type=AuditActionType.LEAD_FIELD_EDIT,
            target_type="lead",
            target_id=self.lead.id,
            before_value={"contact_name": None},
            after_value={"contact_name": "Sam"},
        )

    def test_raw_update_is_rejected_by_database(self):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE audit_entries SET target_id = target_id + 1 WHERE id = %s",
                        [self.entry.id],
                    )

    def test_raw_delete_is_rejected_by_database(self):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM audit_entries WHERE id = %s", [self.entry.id])
