import dashboard.models.operator
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0005_merge_task_2_2"),
    ]

    operations = [
        migrations.AlterModelManagers(
            name="operator",
            managers=[("objects", dashboard.models.operator.OperatorManager())],
        ),
    ]
