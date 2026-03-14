from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("listings", "0014_accessrequest_verification_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="accessrequest",
            name="approval_email_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="borough",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="county",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="decision_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                    ("completed", "Completed"),
                ],
                default="pending",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="last_notification_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="last_notification_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("approval", "Approval"),
                    ("rejection", "Rejection"),
                    ("waitlist", "Wait List"),
                ],
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="manual_decision_reason",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="market_area",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="queue_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "None"),
                    ("manual_review", "Manual Review"),
                    ("waitlist", "Wait List"),
                ],
                default="",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="reason_code",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "None"),
                    ("name_mismatch", "Name mismatch"),
                    ("provider_error", "Provider error"),
                    ("no_match", "No match"),
                    ("expired", "Expired"),
                    ("duplicate_license", "Duplicate license"),
                    ("malformed_provider_response", "Malformed provider response"),
                    ("unsupported_state", "Unsupported state"),
                    ("unsupported_county", "Unsupported county"),
                    ("unsupported_borough", "Unsupported borough"),
                    ("unsupported_market", "Unsupported market"),
                ],
                default="",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="rejection_email_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="reviewed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="reviewed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reviewed_access_requests",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="waitlist_email_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="accessrequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("requested", "Requested"),
                    ("link_sent", "Link Sent"),
                    ("manual_review", "Manual Review"),
                    ("waitlist", "Wait List"),
                    ("completed", "Completed"),
                ],
                default="requested",
                max_length=32,
            ),
        ),
    ]
