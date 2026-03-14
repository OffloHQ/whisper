from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("listings", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Collection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "agent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="collections",
                        to="listings.agentuser",
                    ),
                ),
            ],
            options={
                "ordering": ["name", "-created_at"],
                "unique_together": {("agent", "name")},
            },
        ),
        migrations.CreateModel(
            name="CollectionFilter",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("city", models.CharField(blank=True, max_length=120)),
                ("stage", models.CharField(blank=True, choices=[("premarket", "Premarket"), ("private", "Private")], max_length=20)),
                ("min_beds", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("min_price", models.PositiveIntegerField(blank=True, null=True)),
                ("max_price", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "collection",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="saved_filter",
                        to="listings.collection",
                    ),
                ),
            ],
        ),
    ]
