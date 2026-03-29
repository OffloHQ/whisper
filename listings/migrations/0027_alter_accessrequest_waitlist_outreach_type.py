from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("listings", "0026_waitlistoutreachlog"),
    ]

    operations = [
        migrations.AlterField(
            model_name="accessrequest",
            name="waitlist_outreach_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "None"),
                    ("coming_soon", "Coming Soon"),
                    ("open_signup", "Open in Your Area — Sign Up Now"),
                    ("unsubscribed", "Unsubscribed"),
                    ("removed", "Removed from Waitlist"),
                ],
                default="",
                max_length=32,
            ),
        ),
    ]
