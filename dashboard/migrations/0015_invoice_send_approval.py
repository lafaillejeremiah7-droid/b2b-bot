from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dashboard", "0014_restate_performance_indexes")]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="recipient_email",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="invoice",
            name="provider_invoice_id",
            field=models.TextField(blank=True, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="invoice",
            name="hosted_invoice_url",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="invoice",
            name="sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
