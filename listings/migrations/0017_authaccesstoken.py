from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import listings.models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0016_accessrequest_portal_permissions"),
    ]

    operations = [
        migrations.CreateModel(
            name="AuthAccessToken",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(max_length=254)),
                ("scope", models.CharField(choices=[("sign_in", "Sign In")], default="sign_in", max_length=32)),
                ("delivery_method", models.CharField(choices=[("email", "Email"), ("qr", "QR")], default="email", max_length=16)),
                ("token", models.CharField(default=listings.models.generate_access_token, max_length=128, unique=True)),
                ("expires_at", models.DateTimeField()),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "agent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="auth_access_tokens",
                        to="listings.agentuser",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
