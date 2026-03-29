from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("listings", "0029_accessrequest_access_termination_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="accessrequest",
            name="access_termination_note",
            field=models.TextField(blank=True),
        ),
    ]
