from django.db import migrations


TRIGGER_SQL = r"""
CREATE OR REPLACE FUNCTION dashboard_audit_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit records are immutable (Req 11.4)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_immutable ON audit_entries;
CREATE TRIGGER trg_audit_immutable
BEFORE UPDATE OR DELETE ON audit_entries
FOR EACH ROW EXECUTE FUNCTION dashboard_audit_immutable();

CREATE OR REPLACE FUNCTION dashboard_email_cleared_before_unsubscribe() RETURNS trigger AS $$
DECLARE unsub_at timestamptz;
BEGIN
    SELECT unsubscribed_at INTO unsub_at FROM leads WHERE id = NEW.lead_id;
    IF unsub_at IS NULL THEN RETURN NEW; END IF;
    IF NEW.clearance_timestamp >= unsub_at THEN
        RAISE EXCEPTION 'email clearance must precede unsubscribe (Req 5.19)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_no_email_after_unsubscribe ON emails;
CREATE TRIGGER trg_no_email_after_unsubscribe
BEFORE INSERT ON emails
FOR EACH ROW EXECUTE FUNCTION dashboard_email_cleared_before_unsubscribe();

CREATE OR REPLACE FUNCTION dashboard_email_cleared_before_bounce() RETURNS trigger AS $$
DECLARE current_email text;
BEGIN
    SELECT contact_email INTO current_email FROM leads WHERE id = NEW.lead_id;
    IF current_email IS NULL OR btrim(current_email) = '' THEN RETURN NEW; END IF;
    IF EXISTS (
        SELECT 1 FROM email_bounces b
         WHERE b.lead_id = NEW.lead_id
           AND lower(btrim(b.contact_email)) = lower(btrim(current_email))
           AND b.occurred_at < NEW.clearance_timestamp
    ) THEN
        RAISE EXCEPTION 'email clearance follows a bounce for current address (Req 5.6)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_no_email_after_bounce ON emails;
CREATE TRIGGER trg_no_email_after_bounce
BEFORE INSERT ON emails
FOR EACH ROW EXECUTE FUNCTION dashboard_email_cleared_before_bounce();

CREATE OR REPLACE FUNCTION dashboard_call_cleared_before_dnc() RETURNS trigger AS $$
DECLARE dnc_at timestamptz;
BEGIN
    IF NEW.clearance_timestamp IS NULL THEN RETURN NEW; END IF;
    SELECT do_not_call_at INTO dnc_at FROM leads WHERE id = NEW.lead_id;
    IF dnc_at IS NULL THEN RETURN NEW; END IF;
    IF NEW.clearance_timestamp >= dnc_at THEN
        RAISE EXCEPTION 'call clearance must precede do-not-call (Req 5.20)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_no_call_after_dnc ON calls;
CREATE TRIGGER trg_no_call_after_dnc
BEFORE INSERT ON calls
FOR EACH ROW EXECUTE FUNCTION dashboard_call_cleared_before_dnc();

CREATE OR REPLACE FUNCTION dashboard_outreach_channel_match() RETURNS trigger AS $$
DECLARE reserved_channel text;
DECLARE request_id uuid;
DECLARE expected_channel text;
BEGIN
    request_id := NEW.outreach_request_id;
    IF request_id IS NULL THEN RETURN NEW; END IF;
    expected_channel := CASE WHEN TG_TABLE_NAME = 'emails' THEN 'email' ELSE 'call' END;
    SELECT channel INTO reserved_channel FROM outreach_requests WHERE id = request_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'outreach reservation does not exist (Req 5.12)';
    END IF;
    IF reserved_channel <> expected_channel THEN
        RAISE EXCEPTION 'outreach reservation channel mismatch (Req 5.12)';
    END IF;
    IF expected_channel = 'email' AND EXISTS (
        SELECT 1 FROM calls WHERE outreach_request_id = request_id
    ) THEN
        RAISE EXCEPTION 'outreach request already recorded as call (Req 5.12)';
    END IF;
    IF expected_channel = 'call' AND EXISTS (
        SELECT 1 FROM emails WHERE outreach_request_id = request_id
    ) THEN
        RAISE EXCEPTION 'outreach request already recorded as email (Req 5.12)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_outreach_channel_match ON emails;
CREATE TRIGGER trg_outreach_channel_match
BEFORE INSERT ON emails
FOR EACH ROW EXECUTE FUNCTION dashboard_outreach_channel_match();
DROP TRIGGER IF EXISTS trg_outreach_channel_match ON calls;
CREATE TRIGGER trg_outreach_channel_match
BEFORE INSERT ON calls
FOR EACH ROW EXECUTE FUNCTION dashboard_outreach_channel_match();

CREATE OR REPLACE FUNCTION dashboard_delivery_guard() RETURNS trigger AS $$
DECLARE auth_at timestamptz;
BEGIN
    IF NEW.delivery_sent IS NOT TRUE THEN RETURN NEW; END IF;
    IF NEW.payment_verified_at IS NULL THEN
        RAISE EXCEPTION 'delivery requires payment verification (Req 8.11)';
    END IF;
    SELECT authorized_at INTO auth_at
      FROM release_authorizations WHERE deal_id = NEW.deal_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'delivery requires release authorization (Req 8.11/8.12)';
    END IF;
    IF auth_at < NEW.payment_verified_at THEN
        RAISE EXCEPTION 'authorization precedes payment verification (Req 8.11)';
    END IF;
    IF NEW.delivered_date IS NULL OR NEW.delivered_date < auth_at THEN
        RAISE EXCEPTION 'delivery timestamp precedes authorization (Req 8.11)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_delivery_guard ON deals;
CREATE TRIGGER trg_delivery_guard
BEFORE UPDATE ON deals
FOR EACH ROW EXECUTE FUNCTION dashboard_delivery_guard();

CREATE OR REPLACE FUNCTION dashboard_agreed_price_frozen() RETURNS trigger AS $$
BEGIN
    IF NEW.agreed_price IS DISTINCT FROM OLD.agreed_price
       AND EXISTS (SELECT 1 FROM invoices WHERE deal_id = OLD.deal_id) THEN
        RAISE EXCEPTION 'agreed_price is immutable after invoicing (Req 7.11)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_agreed_price_frozen ON deals;
CREATE TRIGGER trg_agreed_price_frozen
BEFORE UPDATE ON deals
FOR EACH ROW EXECUTE FUNCTION dashboard_agreed_price_frozen();

CREATE OR REPLACE FUNCTION dashboard_deal_state_consistency() RETURNS trigger AS $$
DECLARE lead_state text;
DECLARE verified_at timestamptz;
BEGIN
    IF TG_TABLE_NAME = 'deals' THEN
        SELECT status INTO lead_state FROM leads WHERE id = NEW.lead_id;
        IF lead_state IN ('Payment_Verified', 'Released') AND NEW.payment_verified_at IS NULL THEN
            RAISE EXCEPTION 'payment_verified_at required at verified/released state (Req 8.17-8.20)';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.status IN ('Payment_Verified', 'Released') THEN
        SELECT payment_verified_at INTO verified_at FROM deals WHERE lead_id = NEW.id;
        IF FOUND AND verified_at IS NULL THEN
            RAISE EXCEPTION 'verified/released state requires payment_verified_at (Req 8.17-8.20)';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_deal_state_consistency ON deals;
CREATE TRIGGER trg_deal_state_consistency
BEFORE UPDATE ON deals
FOR EACH ROW EXECUTE FUNCTION dashboard_deal_state_consistency();
DROP TRIGGER IF EXISTS trg_deal_state_consistency ON leads;
CREATE TRIGGER trg_deal_state_consistency
BEFORE UPDATE OF status ON leads
FOR EACH ROW EXECUTE FUNCTION dashboard_deal_state_consistency();

CREATE OR REPLACE FUNCTION dashboard_preview_link_approved() RETURNS trigger AS $$
DECLARE approved timestamptz;
BEGIN
    IF NEW.site_project_id IS NULL THEN RETURN NEW; END IF;
    SELECT approved_at INTO approved FROM site_projects WHERE id = NEW.site_project_id;
    IF NOT FOUND OR approved IS NULL OR approved > NEW.clearance_timestamp THEN
        RAISE EXCEPTION 'preview link requires approval before clearance (Req 6.7)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_preview_link_approved ON emails;
CREATE TRIGGER trg_preview_link_approved
BEFORE INSERT ON emails
FOR EACH ROW EXECUTE FUNCTION dashboard_preview_link_approved();

CREATE OR REPLACE FUNCTION dashboard_site_created_at_immutable() RETURNS trigger AS $$
BEGIN
    IF NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'site_projects.created_at is immutable (Req 6.11/13.5)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_site_created_at_immutable ON site_projects;
CREATE TRIGGER trg_site_created_at_immutable
BEFORE UPDATE ON site_projects
FOR EACH ROW EXECUTE FUNCTION dashboard_site_created_at_immutable();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS trg_site_created_at_immutable ON site_projects;
DROP TRIGGER IF EXISTS trg_preview_link_approved ON emails;
DROP TRIGGER IF EXISTS trg_deal_state_consistency ON leads;
DROP TRIGGER IF EXISTS trg_deal_state_consistency ON deals;
DROP TRIGGER IF EXISTS trg_agreed_price_frozen ON deals;
DROP TRIGGER IF EXISTS trg_delivery_guard ON deals;
DROP TRIGGER IF EXISTS trg_outreach_channel_match ON calls;
DROP TRIGGER IF EXISTS trg_outreach_channel_match ON emails;
DROP TRIGGER IF EXISTS trg_no_call_after_dnc ON calls;
DROP TRIGGER IF EXISTS trg_no_email_after_bounce ON emails;
DROP TRIGGER IF EXISTS trg_no_email_after_unsubscribe ON emails;
DROP TRIGGER IF EXISTS trg_audit_immutable ON audit_entries;
DROP FUNCTION IF EXISTS dashboard_site_created_at_immutable();
DROP FUNCTION IF EXISTS dashboard_preview_link_approved();
DROP FUNCTION IF EXISTS dashboard_deal_state_consistency();
DROP FUNCTION IF EXISTS dashboard_agreed_price_frozen();
DROP FUNCTION IF EXISTS dashboard_delivery_guard();
DROP FUNCTION IF EXISTS dashboard_outreach_channel_match();
DROP FUNCTION IF EXISTS dashboard_call_cleared_before_dnc();
DROP FUNCTION IF EXISTS dashboard_email_cleared_before_bounce();
DROP FUNCTION IF EXISTS dashboard_email_cleared_before_unsubscribe();
DROP FUNCTION IF EXISTS dashboard_audit_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [("dashboard", "0012_schema_indexes_and_genesis")]

    operations = [migrations.RunSQL(TRIGGER_SQL, reverse_sql=REVERSE_SQL)]
