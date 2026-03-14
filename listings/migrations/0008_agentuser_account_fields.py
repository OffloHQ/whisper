from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0007_listing_checkin_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentuser",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="agentuser",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
    ]
