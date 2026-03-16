from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("listings", "0022_agentuser_legal_acceptance"),
    ]

    operations = [
        migrations.AddField(
            model_name="listing",
            name="information_accuracy_certified",
            field=models.BooleanField(default=False),
        ),
    ]
