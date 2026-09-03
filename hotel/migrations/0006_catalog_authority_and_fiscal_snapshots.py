import django.db.models.deletion
from django.db import migrations, models


def populate_fiscal_snapshots(apps, schema_editor):
    BookingStay = apps.get_model("hotel", "BookingStay")
    BookingService = apps.get_model("hotel", "BookingService")

    for stay in BookingStay.objects.select_related("catalog_item").iterator():
        BookingStay.objects.filter(pk=stay.pk).update(
            item_code_snapshot=stay.catalog_item.item_code,
            tax_code_snapshot=stay.catalog_item.tax_code,
        )
    for service in BookingService.objects.select_related("catalog_item").iterator():
        BookingService.objects.filter(pk=service.pk).update(
            item_code_snapshot=service.catalog_item.item_code,
            tax_code_snapshot=service.catalog_item.tax_code,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("hotel", "0005_catalogitem_is_configured"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookingservice",
            name="item_code_snapshot",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="bookingservice",
            name="tax_code_snapshot",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="bookingstay",
            name="item_code_snapshot",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="bookingstay",
            name="tax_code_snapshot",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="catalogitem",
            name="origin",
            field=models.CharField(
                choices=[
                    ("website", "Managed by website"),
                    ("toconline", "Imported from TOConline"),
                ],
                db_index=True,
                default="toconline",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="catalogitem",
            name="remote_fingerprint",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="catalogitem",
            name="supersedes",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="newer_versions",
                to="hotel.catalogitem",
            ),
        ),
        migrations.AddField(
            model_name="catalogitem",
            name="sync_attempted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="catalogitem",
            name="sync_error",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="catalogitem",
            name="sync_status",
            field=models.CharField(
                choices=[
                    ("pending", "Waiting to publish"),
                    ("synced", "Synchronized"),
                    ("diverged", "TOConline differs"),
                    ("failed", "Synchronization failed"),
                ],
                db_index=True,
                default="synced",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="catalogitem",
            name="synced_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="bookingservice",
            name="toconline_item_id",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AlterField(
            model_name="bookingstay",
            name="toconline_item_id",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AlterField(
            model_name="catalogitem",
            name="booking_kind",
            field=models.CharField(
                choices=[
                    ("other", "Not used in bookings"),
                    ("stay", "Accommodation"),
                    ("extra", "Booking extra"),
                ],
                db_index=True,
                default="other",
                help_text=(
                    "Choose whether this catalog item is accommodation, an optional "
                    "booking extra, or only displayed in the public price list."
                ),
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="catalogitem",
            name="toconline_id",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.RunPython(populate_fiscal_snapshots, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="catalogitem",
            constraint=models.UniqueConstraint(
                condition=~models.Q(item_code=""),
                fields=("toconline_type", "item_code"),
                name="unique_catalog_item_code_per_type",
            ),
        ),
    ]
