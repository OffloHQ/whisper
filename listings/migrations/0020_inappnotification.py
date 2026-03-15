from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0019_collection_alerts_and_notification_prefs"),
    ]

    operations = [
        migrations.CreateModel(
            name="InAppNotification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "notification_type",
                    models.CharField(
                        choices=[("collection_match", "Collection Match")],
                        max_length=32,
                    ),
                ),
                ("title", models.CharField(max_length=255)),
                ("body", models.TextField(blank=True)),
                ("link_url", models.CharField(blank=True, max_length=500)),
                ("is_read", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "agent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="in_app_notifications",
                        to="listings.agentuser",
                    ),
                ),
                (
                    "collection",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="in_app_notifications",
                        to="listings.collection",
                    ),
                ),
                (
                    "listing",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="in_app_notifications",
                        to="listings.listing",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="inappnotification",
            constraint=models.UniqueConstraint(
                fields=("agent", "collection", "listing", "notification_type"),
                name="unique_in_app_collection_notification",
            ),
        ),
    ]
