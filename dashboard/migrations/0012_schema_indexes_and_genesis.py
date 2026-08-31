from django.db import migrations


INDEX_SQL = r"""
CREATE UNIQUE INDEX IF NOT EXISTS one_genesis_row_per_lead
    ON pipeline_state_history (lead_id)
    WHERE from_state IS NULL;

CREATE INDEX IF NOT EXISTS idx_leads_status_activity
    ON leads (status, last_activity_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_activity
    ON leads (last_activity_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_company
    ON leads (company_name);
CREATE INDEX IF NOT EXISTS idx_leads_score
    ON leads (researched_score);
CREATE INDEX IF NOT EXISTS idx_leads_email_norm
    ON leads (email_normalized);
CREATE INDEX IF NOT EXISTS idx_leads_phone_digits
    ON leads (phone_digits);
CREATE INDEX IF NOT EXISTS idx_leads_search_trgm
    ON leads USING gin (
      (company_name || ' ' || coalesce(contact_name, '') || ' ' ||
       coalesce(contact_email, '') || ' ' || coalesce(contact_phone, '')) gin_trgm_ops
    );

CREATE INDEX IF NOT EXISTS idx_history_state_time
    ON pipeline_state_history (to_state, occurred_at);
CREATE INDEX IF NOT EXISTS idx_history_lead_time
    ON pipeline_state_history (lead_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_emails_sent
    ON emails (sent_at);
CREATE INDEX IF NOT EXISTS idx_emails_lead_sent
    ON emails (lead_id, sent_at);
CREATE INDEX IF NOT EXISTS idx_calls_time_outcome
    ON calls (timestamp, outcome);
CREATE INDEX IF NOT EXISTS idx_invoices_issued
    ON invoices (issued_at);
CREATE INDEX IF NOT EXISTS idx_payments_paid
    ON payments (paid_date);
CREATE INDEX IF NOT EXISTS idx_deals_verified
    ON deals (payment_verified_at)
    WHERE payment_verified_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_eva_variant
    ON email_variant_assignments (variant_id);

CREATE INDEX IF NOT EXISTS idx_site_projects_lead_created
    ON site_projects (lead_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_audit_time
    ON audit_entries (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor_time
    ON audit_entries (actor_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action_time
    ON audit_entries (action_type, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_target
    ON audit_entries (target_type, target_id);
"""

REVERSE_SQL = r"""
DROP INDEX IF EXISTS idx_audit_target;
DROP INDEX IF EXISTS idx_audit_action_time;
DROP INDEX IF EXISTS idx_audit_actor_time;
DROP INDEX IF EXISTS idx_audit_time;
DROP INDEX IF EXISTS idx_site_projects_lead_created;
DROP INDEX IF EXISTS idx_eva_variant;
DROP INDEX IF EXISTS idx_deals_verified;
DROP INDEX IF EXISTS idx_payments_paid;
DROP INDEX IF EXISTS idx_invoices_issued;
DROP INDEX IF EXISTS idx_calls_time_outcome;
DROP INDEX IF EXISTS idx_emails_lead_sent;
DROP INDEX IF EXISTS idx_emails_sent;
DROP INDEX IF EXISTS idx_history_lead_time;
DROP INDEX IF EXISTS idx_history_state_time;
DROP INDEX IF EXISTS idx_leads_search_trgm;
DROP INDEX IF EXISTS idx_leads_phone_digits;
DROP INDEX IF EXISTS idx_leads_email_norm;
DROP INDEX IF EXISTS idx_leads_score;
DROP INDEX IF EXISTS idx_leads_company;
DROP INDEX IF EXISTS idx_leads_activity;
DROP INDEX IF EXISTS idx_leads_status_activity;
DROP INDEX IF EXISTS one_genesis_row_per_lead;
"""


class Migration(migrations.Migration):
    dependencies = [("dashboard", "0011_normalize_history_actor_constraint")]

    operations = [migrations.RunSQL(INDEX_SQL, reverse_sql=REVERSE_SQL)]
