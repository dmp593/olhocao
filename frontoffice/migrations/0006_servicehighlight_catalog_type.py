from django.db import migrations, models
import django.db.models.deletion


def map_seeded_highlights_to_catalog_types(apps, schema_editor):
    """Map historical seed content once; runtime behavior never depends on these names."""

    ServiceHighlight = apps.get_model("frontoffice", "ServiceHighlight")
    CatalogType = apps.get_model("catalog", "CatalogType")
    mappings = {
        "hotel": "hotel",
        "daycare": "atl-canino",
        "training": "treino-e-obediencia",
        "grooming": "banhos-e-grooming",
    }
    for icon, slug in mappings.items():
        catalog_type = CatalogType.objects.filter(slug=slug).first()
        if catalog_type is None:
            continue
        ServiceHighlight.objects.filter(icon=icon).update(
            catalog_type=catalog_type,
            destination="",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_seed_catalog_hierarchy"),
        ("frontoffice", "0005_seed_public_website_content"),
    ]

    operations = [
        migrations.AddField(
            model_name="servicehighlight",
            name="catalog_type",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Optional catalog destination. The link follows this type even when its "
                    "name changes."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="homepage_highlights",
                to="catalog.catalogtype",
            ),
        ),
        migrations.AlterField(
            model_name="servicehighlight",
            name="destination",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "No link"),
                    ("pricing", "Prices"),
                    ("hotel", "Hotel booking"),
                    ("activities", "Daycare and training"),
                    ("contact", "Contact"),
                    ("about", "About us"),
                ],
                help_text="Use only for a page that is not represented by a catalog type.",
                max_length=20,
            ),
        ),
        migrations.RunPython(map_seeded_highlights_to_catalog_types, migrations.RunPython.noop),
    ]
