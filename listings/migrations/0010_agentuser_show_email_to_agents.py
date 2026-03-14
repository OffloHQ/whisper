from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0009_agentemail_agentphone"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentuser",
            name="show_email_to_agents",
            field=models.BooleanField(default=False),
        ),
    ]
