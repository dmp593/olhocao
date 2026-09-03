from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("discounts", "0003_remove_legacy_catalog_scopes")]

    operations = [
        migrations.AlterField(
            model_name="promotion",
            name="service_starts_on",
            field=models.DateField(
                blank=True,
                help_text="Optional first eligible service date.",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="promotion",
            name="service_ends_on",
            field=models.DateField(
                blank=True,
                help_text="Optional last eligible service date.",
                null=True,
            ),
        ),
    ]
