from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("listings", "0024_listing_certification_timestamps"),
    ]

    operations = [
        migrations.AddField(
            model_name="accessrequest",
            name="waitlist_outreach_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "None"),
                    ("coming_soon", "Coming Soon"),
                    ("open_signup", "Open in Your Area — Sign Up Now"),
                ],
                default="",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="waitlist_outreach_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="waitlist_outreach_sent_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="sent_waitlist_outreach_requests",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="waitlist_removed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="waitlist_removed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="removed_waitlist_requests",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="waitlist_unsubscribed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
