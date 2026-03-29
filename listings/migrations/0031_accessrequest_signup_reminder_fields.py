from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("listings", "0030_accessrequest_access_termination_note"),
    ]

    operations = [
        migrations.AddField(
            model_name="accessrequest",
            name="signup_final_reminder_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="signup_reminder_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
