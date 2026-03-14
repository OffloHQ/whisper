from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0005_collectionfilter_min_baths"),
    ]

    operations = [
        migrations.CreateModel(
            name="CollectionItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("collection", models.ForeignKey(on_delete=models.CASCADE, related_name="items", to="listings.collection")),
                ("listing", models.ForeignKey(on_delete=models.CASCADE, related_name="collection_items", to="listings.listing")),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="collectionitem",
            constraint=models.UniqueConstraint(fields=("collection", "listing"), name="unique_listing_per_collection"),
        ),
    ]
