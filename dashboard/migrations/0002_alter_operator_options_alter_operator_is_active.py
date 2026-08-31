from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0001_initial"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="operator",
            options={"verbose_name": "user", "verbose_name_plural": "users"},
        ),
        migrations.AlterField(
            model_name="operator",
            name="is_active",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Designates whether this user should be treated as active. "
                    "Unselect this instead of deleting accounts."
                ),
                verbose_name="active",
            ),
        ),
    ]
