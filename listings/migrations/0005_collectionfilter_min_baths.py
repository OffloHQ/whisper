from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0004_listing_last_confirmed_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="collectionfilter",
            name="min_baths",
            field=models.DecimalField(blank=True, decimal_places=1, max_digits=3, null=True),
        ),
    ]
