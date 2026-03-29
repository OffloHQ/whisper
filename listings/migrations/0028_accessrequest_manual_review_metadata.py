from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("listings", "0027_alter_accessrequest_waitlist_outreach_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="accessrequest",
            name="manual_verification_approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="manual_verification_evidence_ref",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="manual_verification_rejected_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
