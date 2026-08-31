from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0015_invoice_send_approval"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="sent_by_operator",
            field=models.ForeignKey(
                blank=True,
                db_column="sent_by_operator_id",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sent_invoices",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
