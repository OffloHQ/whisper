from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0015_accessrequest_intake_fields"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="accessrequest",
            options={
                "ordering": ["-updated_at", "-created_at"],
                "permissions": (
                    ("can_access_intake_portal", "Can access intake portal"),
                    ("can_review_manual_requests", "Can review manual intake requests"),
                    ("can_manage_waitlist", "Can manage intake waitlist"),
                ),
            },
        ),
    ]
