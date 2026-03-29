from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("listings", "0025_accessrequest_waitlist_outreach_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="WaitlistOutreachLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "outreach_type",
                    models.CharField(
                        choices=[
                            ("", "None"),
                            ("coming_soon", "Coming Soon"),
                            ("open_signup", "Open in Your Area — Sign Up Now"),
                            ("unsubscribed", "Unsubscribed"),
                            ("removed", "Removed from Waitlist"),
                        ],
                        max_length=32,
                    ),
                ),
                ("sent_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("note", models.TextField(blank=True)),
                (
                    "access_request",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="waitlist_outreach_logs",
                        to="listings.accessrequest",
                    ),
                ),
                (
                    "sent_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="waitlist_outreach_logs_sent",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-sent_at", "-id"]},
        ),
    ]
