from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0018_authaccesstoken_qr_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentuser",
            name="collection_match_emails",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="agentuser",
            name="freshness_reminder_emails",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="agentuser",
            name="product_update_emails",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="collection",
            name="notifications_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="EmailNotificationLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("recipient_email", models.EmailField(max_length=254)),
                (
                    "notification_type",
                    models.CharField(
                        choices=[("collection_match", "Collection Match")],
                        max_length=32,
                    ),
                ),
                ("sent_at", models.DateTimeField(auto_now_add=True)),
                (
                    "agent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="email_notification_logs",
                        to="listings.agentuser",
                    ),
                ),
                (
                    "collection",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="email_notification_logs",
                        to="listings.collection",
                    ),
                ),
                (
                    "listing",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="email_notification_logs",
                        to="listings.listing",
                    ),
                ),
            ],
            options={"ordering": ["-sent_at"]},
        ),
        migrations.AddConstraint(
            model_name="emailnotificationlog",
            constraint=models.UniqueConstraint(
                fields=("collection", "listing", "notification_type"),
                name="unique_collection_listing_notification",
            ),
        ),
    ]
