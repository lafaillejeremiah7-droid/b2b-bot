from django.db import migrations


class Migration(migrations.Migration):
    """Compatibility node for the proven Task 2.1 Lead migration.

    The primary branch created Operator under a different migration name. Keeping
    this no-op node lets the previously tested Lead migration remain byte-for-byte
    unchanged while the graph still points at the already-created Operator state.
    """

    dependencies = [
        ("dashboard", "0002_alter_operator_options_alter_operator_is_active"),
    ]

    operations = []
