from django.db import migrations


def map_dynamic_catalog(apps, schema_editor):
    CatalogOffer = apps.get_model("catalog", "CatalogOffer")
    CatalogType = apps.get_model("catalog", "CatalogType")
    ServiceOffering = apps.get_model("activities", "ServiceOffering")
    ServiceEntitlement = apps.get_model("activities", "ServiceEntitlement")
    ActivitySession = apps.get_model("activities", "ActivitySession")

    offers_by_item = {
        offer.catalog_item_id: offer
        for offer in CatalogOffer.objects.select_related("catalog_type", "level")
    }
    for offering in ServiceOffering.objects.all():
        offer = offers_by_item.get(offering.catalog_item_id)
        if offer is None:
            raise RuntimeError(f"Catalog offer missing for service offering {offering.pk}")
        offering.catalog_offer_id = offer.pk
        offering.save(update_fields=("catalog_offer",))

    offerings = {
        offering.pk: offering
        for offering in ServiceOffering.objects.select_related(
            "catalog_offer__catalog_type",
            "catalog_offer__level",
        )
    }
    for entitlement in ServiceEntitlement.objects.all():
        offering = offerings[entitlement.offering_id]
        catalog_offer = offering.catalog_offer
        entitlement.catalog_type_reference = catalog_offer.catalog_type_id
        entitlement.catalog_type_name_snapshot = catalog_offer.catalog_type.name
        entitlement.catalog_level_reference = catalog_offer.level_id
        entitlement.catalog_level_name_snapshot = (
            catalog_offer.level.name if catalog_offer.level_id else ""
        )
        entitlement.save(
            update_fields=(
                "catalog_type_reference",
                "catalog_type_name_snapshot",
                "catalog_level_reference",
                "catalog_level_name_snapshot",
            )
        )

    type_by_legacy_value = {
        "daycare": CatalogType.objects.get(workflow="full_day_slot"),
        "training": CatalogType.objects.get(workflow="timed_slot"),
    }
    for session in ActivitySession.objects.all():
        catalog_type = type_by_legacy_value[session.activity_type]
        session.catalog_type_id = catalog_type.pk
        session.save(update_fields=("catalog_type",))
        if session.requires_exclusive_access:
            session.eligible_levels.set(catalog_type.levels.filter(slug="premium"))


def restore_legacy_catalog_values(apps, schema_editor):
    ServiceOffering = apps.get_model("activities", "ServiceOffering")
    ServiceEntitlement = apps.get_model("activities", "ServiceEntitlement")
    ActivitySession = apps.get_model("activities", "ActivitySession")

    for offering in ServiceOffering.objects.select_related(
        "catalog_offer__catalog_type",
        "catalog_offer__level",
    ):
        catalog_offer = offering.catalog_offer
        workflow = catalog_offer.catalog_type.workflow
        offering.catalog_item_id = catalog_offer.catalog_item_id
        offering.activity_type = "daycare" if workflow == "full_day_slot" else "training"
        offering.grants_exclusive_access = bool(
            catalog_offer.level_id and catalog_offer.level.slug == "premium"
        )
        offering.save(
            update_fields=("catalog_item", "activity_type", "grants_exclusive_access")
        )

    for entitlement in ServiceEntitlement.objects.select_related("offering"):
        entitlement.activity_type_snapshot = entitlement.offering.activity_type
        entitlement.grants_exclusive_access_snapshot = entitlement.offering.grants_exclusive_access
        entitlement.save(
            update_fields=("activity_type_snapshot", "grants_exclusive_access_snapshot")
        )

    for session in ActivitySession.objects.select_related("catalog_type"):
        session.activity_type = (
            "daycare" if session.catalog_type.workflow == "full_day_slot" else "training"
        )
        session.requires_exclusive_access = session.eligible_levels.exists()
        session.save(update_fields=("activity_type", "requires_exclusive_access"))


class Migration(migrations.Migration):
    dependencies = [("activities", "0011_catalog_calendar_foundation")]

    operations = [migrations.RunPython(map_dynamic_catalog, restore_legacy_catalog_values)]
