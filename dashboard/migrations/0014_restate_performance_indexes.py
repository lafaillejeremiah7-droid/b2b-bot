from django.db import migrations


FORWARD = r"""
DROP INDEX IF EXISTS idx_eva_variant;
CREATE INDEX idx_eva_variant
    ON email_variant_assignments (variant_id, email_id);

DROP INDEX IF EXISTS idx_audit_time;
CREATE INDEX idx_audit_time
    ON audit_entries (occurred_at DESC, id DESC);

DROP INDEX IF EXISTS idx_audit_target;
CREATE INDEX idx_audit_target
    ON audit_entries (target_type, target_id, occurred_at DESC);
"""

REVERSE = r"""
DROP INDEX IF EXISTS idx_eva_variant;
CREATE INDEX idx_eva_variant
    ON email_variant_assignments (variant_id);

DROP INDEX IF EXISTS idx_audit_time;
CREATE INDEX idx_audit_time
    ON audit_entries (occurred_at DESC);

DROP INDEX IF EXISTS idx_audit_target;
CREATE INDEX idx_audit_target
    ON audit_entries (target_type, target_id);
"""


class Migration(migrations.Migration):
    dependencies = [("dashboard", "0013_install_enforcement_triggers")]
    operations = [migrations.RunSQL(FORWARD, reverse_sql=REVERSE)]
