from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0010_merge_task_2_3"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="pipelinestatehistory",
            name="history_actor_shape_matches_kind",
        ),
        migrations.AddConstraint(
            model_name="pipelinestatehistory",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("actor__isnull", False), ("actor_kind", "operator")),
                    models.Q(
                        ("actor__isnull", True),
                        ("actor_kind", "adapter_event"),
                        ("source_event_id__isnull", False),
                    ),
                    _connector="OR",
                ),
                name="history_actor_shape_matches_kind",
            ),
        ),
    ]
