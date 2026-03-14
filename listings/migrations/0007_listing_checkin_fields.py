from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0006_collectionitem"),
    ]

    operations = [
        migrations.AddField(
            model_name="listing",
            name="last_reminder_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="listing",
            name="reminder_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="listing",
            name="removed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="listing",
            name="removed_reason",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name="listing",
            name="status",
            field=models.CharField(
                choices=[
                    ("active", "Active"),
                    ("removed_by_agent", "Removed by agent"),
                    ("stale", "Stale"),
                ],
                default="active",
                max_length=20,
            ),
        ),
    ]
