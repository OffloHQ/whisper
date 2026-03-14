from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0011_alter_listing_description_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentuser",
            name="brokerage",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="agentuser",
            name="city",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="agentuser",
            name="signup_status",
            field=models.CharField(
                choices=[
                    ("active", "Active"),
                    ("pending_contact", "Pending Contact"),
                    ("manual_review", "Manual Review"),
                ],
                default="active",
                max_length=32,
            ),
        ),
        migrations.CreateModel(
            name="AccessRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(max_length=254, unique=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("requested", "Requested"),
                            ("link_sent", "Link Sent"),
                            ("manual_review", "Manual Review"),
                            ("completed", "Completed"),
                        ],
                        default="requested",
                        max_length=32,
                    ),
                ),
                ("signup_sent_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-updated_at", "-created_at"]},
        ),
    ]
