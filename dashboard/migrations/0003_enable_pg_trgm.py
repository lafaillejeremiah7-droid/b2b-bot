from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0002_alter_operator_options_alter_operator_is_active"),
    ]

    operations = [
        TrigramExtension(),
    ]
