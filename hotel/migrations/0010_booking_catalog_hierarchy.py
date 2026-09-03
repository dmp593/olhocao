import django.db.models.deletion
from django.db import migrations, models


def populate_catalog_references(apps, schema_editor):
    CatalogOffer = apps.get_model("catalog", "CatalogOffer")
    BookingStay = apps.get_model("hotel", "BookingStay")
    BookingService = apps.get_model("hotel", "BookingService")

    offers_by_item_id = {
        offer.catalog_item_id: offer
        for offer in CatalogOffer.objects.select_related("catalog_type", "level")
    }

    for model in (BookingStay, BookingService):
        for purchase_line in model.objects.all().iterator():
            offer = offers_by_item_id.get(purchase_line.catalog_item_id)
            if offer is None:
                raise RuntimeError(
                    "Every historical booking line must be assigned to a catalog "
                    f"offer before migration (catalog item {purchase_line.catalog_item_id})."
                )
            purchase_line.catalog_offer_id = offer.pk
            purchase_line.catalog_type_reference = offer.catalog_type_id
            purchase_line.catalog_type_name_snapshot = offer.catalog_type.name
            purchase_line.catalog_level_reference = offer.level_id
            purchase_line.catalog_level_name_snapshot = (
                offer.level.name if offer.level_id else ""
            )
            purchase_line.save(
                update_fields=(
                    "catalog_offer",
                    "catalog_type_reference",
                    "catalog_type_name_snapshot",
                    "catalog_level_reference",
                    "catalog_level_name_snapshot",
                )
            )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_seed_catalog_hierarchy"),
        ("hotel", "0009_bookingpriceadjustment_booking_confirmed_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookingstay",
            name="catalog_offer",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="booking_stays",
                to="catalog.catalogoffer",
            ),
        ),
        migrations.AddField(
            model_name="bookingstay",
            name="catalog_type_reference",
            field=models.PositiveBigIntegerField(editable=False, null=True),
        ),
        migrations.AddField(
            model_name="bookingstay",
            name="catalog_type_name_snapshot",
            field=models.CharField(editable=False, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name="bookingstay",
            name="catalog_level_reference",
            field=models.PositiveBigIntegerField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="bookingstay",
            name="catalog_level_name_snapshot",
            field=models.CharField(blank=True, editable=False, max_length=120),
        ),
        migrations.AddField(
            model_name="bookingservice",
            name="catalog_offer",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="booking_services",
                to="catalog.catalogoffer",
            ),
        ),
        migrations.AddField(
            model_name="bookingservice",
            name="catalog_type_reference",
            field=models.PositiveBigIntegerField(editable=False, null=True),
        ),
        migrations.AddField(
            model_name="bookingservice",
            name="catalog_type_name_snapshot",
            field=models.CharField(editable=False, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name="bookingservice",
            name="catalog_level_reference",
            field=models.PositiveBigIntegerField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="bookingservice",
            name="catalog_level_name_snapshot",
            field=models.CharField(blank=True, editable=False, max_length=120),
        ),
        migrations.AddField(
            model_name="bookingservice",
            name="schedule_preference",
            field=models.CharField(
                choices=[
                    ("unscheduled", "No date preference"),
                    ("date", "Preferred date"),
                    ("date_time", "Preferred date and time"),
                ],
                default="unscheduled",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="bookingservice",
            name="preferred_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="bookingservice",
            name="preferred_time",
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.RunPython(populate_catalog_references, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="bookingstay",
            name="catalog_offer",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="booking_stays",
                to="catalog.catalogoffer",
            ),
        ),
        migrations.AlterField(
            model_name="bookingstay",
            name="catalog_type_reference",
            field=models.PositiveBigIntegerField(editable=False),
        ),
        migrations.AlterField(
            model_name="bookingstay",
            name="catalog_type_name_snapshot",
            field=models.CharField(editable=False, max_length=120),
        ),
        migrations.AlterField(
            model_name="bookingservice",
            name="catalog_offer",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="booking_services",
                to="catalog.catalogoffer",
            ),
        ),
        migrations.AlterField(
            model_name="bookingservice",
            name="catalog_type_reference",
            field=models.PositiveBigIntegerField(editable=False),
        ),
        migrations.AlterField(
            model_name="bookingservice",
            name="catalog_type_name_snapshot",
            field=models.CharField(editable=False, max_length=120),
        ),
        migrations.AddIndex(
            model_name="bookingstay",
            index=models.Index(
                fields=["catalog_offer", "start_date", "end_date"],
                name="stay_offer_date_range_idx",
            ),
        ),
    ]
