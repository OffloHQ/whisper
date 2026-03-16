from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0021_listing_certifications"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentuser",
            name="legal_acceptance_ip",
            field=models.GenericIPAddressField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="agentuser",
            name="legal_acceptance_user_agent",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="agentuser",
            name="privacy_accepted",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="agentuser",
            name="privacy_accepted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="agentuser",
            name="privacy_version",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="agentuser",
            name="terms_accepted",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="agentuser",
            name="terms_accepted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="agentuser",
            name="terms_version",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
