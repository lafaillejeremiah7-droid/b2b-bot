from django.db import migrations


class Migration(migrations.Migration):
    """Join the two independent Task 2.3 migration branches.

    ``0009_restate_site_rejection_constraint`` fixes the already-created
    SiteProject constraint while ``0009_create_remaining_task_2_3`` creates the
    remaining bookkeeping tables. They touch independent state, so a no-op merge
    is the correct migration-graph representation.
    """

    dependencies = [
        ("dashboard", "0009_create_remaining_task_2_3"),
        ("dashboard", "0009_restate_site_rejection_constraint"),
    ]

    operations = []
