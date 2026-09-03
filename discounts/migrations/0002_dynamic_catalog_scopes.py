from django.db import migrations, models


LEGACY_CHANNEL_MAP = {
    "hotel_stay": "date_range",
    "hotel_extra": "add_on",
    "daycare_pack": "pack",
    "daycare_subscription": "subscription",
    "training_pack": "pack",
    "training_subscription": "subscription",
}


def migrate_promotion_scopes(apps, schema_editor):
    Promotion = apps.get_model("discounts", "Promotion")
    CatalogOffer = apps.get_model("catalog", "CatalogOffer")
    CatalogType = apps.get_model("catalog", "CatalogType")

    offers_by_item = {
        offer.catalog_item_id: offer
        for offer in CatalogOffer.objects.select_related("catalog_type", "level")
    }
    types_by_workflow = {
        workflow: list(CatalogType.objects.filter(workflow=workflow))
        for workflow in ("date_range", "full_day_slot", "timed_slot")
    }

    for promotion in Promotion.objects.prefetch_related("catalog_items"):
        legacy_contexts = set(promotion.eligible_contexts or [])
        promotion.eligible_sale_channels = list(
            dict.fromkeys(
                LEGACY_CHANNEL_MAP[context]
                for context in promotion.eligible_contexts or []
                if context in LEGACY_CHANNEL_MAP
            )
        )
        promotion.save(update_fields=("eligible_sale_channels",))

        if "hotel_stay" in legacy_contexts:
            promotion.catalog_types.add(*types_by_workflow["date_range"])
        if legacy_contexts.intersection({"daycare_pack", "daycare_subscription"}):
            promotion.catalog_types.add(*types_by_workflow["full_day_slot"])
        if legacy_contexts.intersection({"training_pack", "training_subscription"}):
            promotion.catalog_types.add(*types_by_workflow["timed_slot"])
        if "hotel_extra" in legacy_contexts:
            promotion.host_catalog_types.add(*types_by_workflow["date_range"])

        mapped_offers = [
            offers_by_item[item.pk]
            for item in promotion.catalog_items.all()
            if item.pk in offers_by_item
        ]
        if mapped_offers:
            promotion.catalog_offers.add(*mapped_offers)


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_seed_catalog_hierarchy"),
        ("discounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="promotion",
            name="eligible_sale_channels",
            field=models.JSONField(
                default=list,
                help_text="The stable purchase flows where this promotion can be used.",
            ),
        ),
        migrations.AddField(
            model_name="promotion",
            name="catalog_types",
            field=models.ManyToManyField(
                blank=True,
                help_text="Leave empty to include offers from every catalog type.",
                related_name="promotions",
                to="catalog.catalogtype",
            ),
        ),
        migrations.AddField(
            model_name="promotion",
            name="catalog_levels",
            field=models.ManyToManyField(
                blank=True,
                help_text="Leave empty to include every level in the selected catalog types.",
                related_name="promotions",
                to="catalog.cataloglevel",
            ),
        ),
        migrations.AddField(
            model_name="promotion",
            name="catalog_offers",
            field=models.ManyToManyField(
                blank=True,
                help_text="Leave empty to include every matching offer.",
                related_name="promotions",
                to="catalog.catalogoffer",
            ),
        ),
        migrations.AddField(
            model_name="promotion",
            name="host_catalog_types",
            field=models.ManyToManyField(
                blank=True,
                help_text="Optional. Restrict an add-on to the service in which it is purchased.",
                related_name="hosted_add_on_promotions",
                to="catalog.catalogtype",
            ),
        ),
        migrations.RunPython(migrate_promotion_scopes, migrations.RunPython.noop),
    ]
