from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0013_agentuser_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="accessrequest",
            name="full_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="license_number",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="matched_business_city",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="matched_business_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="matched_business_state",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="matched_expiration_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="matched_license_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="matched_license_type",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="requires_manual_review",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="state",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="verification_attempted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="verification_payload",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="verification_provider",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="verification_reason",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="verification_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("verified", "Verified"),
                    ("manual_review", "Manual Review"),
                    ("unsupported_state", "Unsupported State"),
                    ("provider_error", "Provider Error"),
                    ("no_match", "No Match"),
                    ("expired", "Expired"),
                    ("name_mismatch", "Name Mismatch"),
                ],
                default="pending",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
