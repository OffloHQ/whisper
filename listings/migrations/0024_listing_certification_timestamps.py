from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("listings", "0023_listing_information_accuracy_certified"),
    ]

    operations = [
        migrations.AddField(
            model_name="listing",
            name="agent_compliance_acknowledged_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="listing",
            name="information_accuracy_certified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="listing",
            name="private_marketing_certified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="listing",
            name="seller_direction_certified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
