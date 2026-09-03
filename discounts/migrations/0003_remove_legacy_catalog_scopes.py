from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("discounts", "0002_dynamic_catalog_scopes")]

    operations = [
        migrations.RemoveField(model_name="promotion", name="catalog_items"),
        migrations.RemoveField(model_name="promotion", name="eligible_contexts"),
    ]
