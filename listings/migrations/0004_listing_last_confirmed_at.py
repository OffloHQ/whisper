from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [
        ("listings", "0003_savedlisting"),
    ]

    operations = [
        migrations.AddField(
            model_name="listing",
            name="last_confirmed_at",
            field=models.DateTimeField(default=timezone.now),
        ),
    ]
