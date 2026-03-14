from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0017_authaccesstoken"),
    ]

    operations = [
        migrations.AddField(
            model_name="authaccesstoken",
            name="completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="authaccesstoken",
            name="desktop_authenticated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
