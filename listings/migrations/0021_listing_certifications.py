from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0020_inappnotification"),
    ]

    operations = [
        migrations.AddField(
            model_name="listing",
            name="agent_compliance_acknowledged",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="listing",
            name="private_marketing_certified",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="listing",
            name="seller_direction_certified",
            field=models.BooleanField(default=False),
        ),
    ]
