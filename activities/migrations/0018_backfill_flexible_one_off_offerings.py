from django.db import migrations


def create_missing_flexible_offerings(apps, schema_editor):
    CatalogOffer = apps.get_model("catalog", "CatalogOffer")
    ServiceOffering = apps.get_model("activities", "ServiceOffering")

    offers = CatalogOffer.objects.filter(
        catalog_type__workflow="flexible",
        activity_offering__isnull=True,
    ).select_related("catalog_type", "level")
    for offer in offers.iterator():
        cancellation_notice_hours = (
            offer.level.cancellation_notice_hours_override
            if offer.level_id
            and offer.level.cancellation_notice_hours_override is not None
            else offer.catalog_type.default_cancellation_notice_hours
        )
        ServiceOffering.objects.create(
            catalog_offer=offer,
            mode="one_off",
            cancellation_notice_hours=cancellation_notice_hours,
            is_published=offer.is_public,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("activities", "0017_activity_reservation_add_on_origin"),
    ]

    operations = [
        migrations.RunPython(
            create_missing_flexible_offerings,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
