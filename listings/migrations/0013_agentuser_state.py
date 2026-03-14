from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0012_accessrequest_agentuser_signup_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentuser",
            name="state",
            field=models.CharField(blank=True, max_length=32),
        ),
    ]
