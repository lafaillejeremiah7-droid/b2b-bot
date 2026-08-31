from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0006_alter_operator_managers"),
    ]

    operations = [
        migrations.AlterModelTable(
            name="operator",
            table="operators",
        ),
    ]
