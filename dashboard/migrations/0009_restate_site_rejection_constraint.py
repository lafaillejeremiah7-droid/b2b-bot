import dashboard.models.constraints
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0008_create_core_task_2_3"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="siteproject",
            name="site_projects_rejection_reason_matches_state",
        ),
        migrations.AddConstraint(
            model_name="siteproject",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        review_state="Rejected",
                        rejection_reason__isnull=False,
                    )
                    & dashboard.models.constraints.length_between(
                        "rejection_reason", 10, 1000
                    )
                )
                | (
                    ~models.Q(review_state="Rejected")
                    & models.Q(rejection_reason__isnull=True)
                ),
                name="site_projects_rejection_reason_matches_state",
            ),
        ),
    ]
