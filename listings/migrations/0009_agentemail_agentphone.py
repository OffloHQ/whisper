from django.db import migrations, models
from django.utils import timezone


def seed_primary_emails(apps, schema_editor):
    AgentUser = apps.get_model("listings", "AgentUser")
    AgentEmail = apps.get_model("listings", "AgentEmail")

    for agent in AgentUser.objects.all():
        AgentEmail.objects.update_or_create(
            agent=agent,
            email=agent.email,
            defaults={
                "is_verified": True,
                "is_primary": True,
                "verified_at": agent.created_at or timezone.now(),
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0008_agentuser_account_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentEmail",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(max_length=254, unique=True)),
                ("is_verified", models.BooleanField(default=False)),
                ("is_primary", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("agent", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="emails", to="listings.agentuser")),
            ],
            options={"ordering": ["-is_primary", "-is_verified", "email"]},
        ),
        migrations.CreateModel(
            name="AgentPhone",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("phone_number", models.CharField(max_length=30)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("agent", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="phones", to="listings.agentuser")),
            ],
            options={"ordering": ["created_at", "pk"]},
        ),
        migrations.RunPython(seed_primary_emails, migrations.RunPython.noop),
    ]
