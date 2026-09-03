from decimal import Decimal

from django.test import TestCase

from hotel.models import (
    CatalogBookingKind,
    CatalogItem,
    CatalogItemOrigin,
    CatalogItemType,
    CatalogSyncStatus,
)
from hotel.services.catalog import CatalogSyncError, TocOnlineCatalogSynchronizer
from hotel.tests.factories import create_account, create_booking, create_catalog_item


class FakeCatalogClient:
    def __init__(
        self,
        *,
        services=None,
        products=None,
        families=None,
        fail_create=False,
    ):
        self.services = services or []
        self.products = products or []
        self.families = families or []
        self.fail_create = fail_create
        self.created = []

    def list_services(self):
        return self.services

    def list_products(self):
        return self.products

    def list_item_families(self):
        return self.families

    def find_catalog_item(self, item_type, *, item_code):
        items = self.services if item_type == CatalogItemType.SERVICE else self.products
        return next(
            (
                item
                for item in items
                if (item.get("attributes") or {}).get("item_code") == item_code
            ),
            None,
        )

    def create_catalog_item(self, item_type, **attributes):
        if self.fail_create:
            raise RuntimeError("TOConline unavailable")
        self.created.append((item_type, attributes))
        return {
            "id": f"published-{len(self.created)}",
            "attributes": {
                "item_code": attributes["item_code"],
                "item_description": attributes["item_description"],
                "sales_price": attributes["sales_price"],
                "sales_price_includes_vat": attributes["sales_price_includes_vat"],
                "tax_code": attributes["tax_code"],
            },
        }


def remote_item(
    remote_id,
    *,
    price="10.00",
    gross_price="12.30",
    name="Day care",
    active=True,
    family_id="family-1",
    item_code=None,
):
    return {
        "id": remote_id,
        "attributes": {
            "item_code": item_code or f"CODE-{remote_id}",
            "item_description": name,
            "notes": "Public description",
            "sales_price": price,
            "sales_price_vat_display": gross_price,
            "sales_price_includes_vat": False,
            "applied_tax_code": "NOR",
            "is_active": active,
        },
        "relationships": {
            "item_families": {"data": {"id": family_id}},
        },
    }


class CatalogSynchronizationTests(TestCase):
    def create_website_item(self, **overrides):
        values = {
            "toconline_type": CatalogItemType.SERVICE,
            "item_code": "OC-S-00000001",
            "name": "ATL Gold",
            "unit_price_eur": Decimal("49.90"),
            "tax_code": "NOR",
            "origin": CatalogItemOrigin.WEBSITE,
            "sync_status": CatalogSyncStatus.PENDING,
            "booking_kind": CatalogBookingKind.OTHER,
        }
        values.update(overrides)
        return CatalogItem.objects.create(**values)

    def test_catalog_item_has_deterministic_fallback_artwork(self):
        stay = create_catalog_item()
        extra = create_catalog_item(
            remote_id="extra",
            booking_kind=CatalogBookingKind.EXTRA,
        )

        self.assertEqual(
            stay.fallback_image_static_path,
            "images/services/hotel-stay.webp",
        )
        self.assertEqual(
            extra.fallback_image_static_path,
            "images/services/training.webp",
        )

    def test_creates_projection_with_vat_included_price(self):
        client = FakeCatalogClient(
            services=[remote_item("10")],
            families=[{"id": "family-1", "attributes": {"name": "Hotel"}}],
        )

        result = TocOnlineCatalogSynchronizer(client).synchronize()

        item = CatalogItem.objects.get()
        self.assertEqual(result.created, 1)
        self.assertEqual(item.unit_price_eur, Decimal("12.30"))
        self.assertEqual(item.tax_code, "NOR")
        self.assertEqual(item.family_name, "Hotel")
        self.assertEqual(item.description, "Public description")
        self.assertEqual(item.booking_kind, CatalogBookingKind.OTHER)

    def test_preserves_local_image_and_booking_classification_on_update(self):
        item = create_catalog_item(remote_id="10")
        item.image = "catalog/custom/image.png"
        item.is_public = True
        item.save(update_fields=("image", "is_public"))
        client = FakeCatalogClient(
            services=[remote_item("10", gross_price="25.00", name="New name")]
        )

        result = TocOnlineCatalogSynchronizer(client).synchronize()

        item.refresh_from_db()
        self.assertEqual(result.updated, 1)
        self.assertEqual(item.name, "New name")
        self.assertEqual(item.unit_price_eur, Decimal("25.00"))
        self.assertEqual(item.booking_kind, CatalogBookingKind.STAY)
        self.assertTrue(item.is_public)
        self.assertEqual(item.image.name, "catalog/custom/image.png")

    def test_missing_remote_item_is_soft_deactivated(self):
        missing = create_catalog_item(remote_id="missing")
        client = FakeCatalogClient(services=[remote_item("present")])

        result = TocOnlineCatalogSynchronizer(client).synchronize()

        missing.refresh_from_db()
        self.assertEqual(result.deactivated, 1)
        self.assertFalse(missing.is_active)
        self.assertIsNotNone(missing.remote_deleted_at)

    def test_reappearing_remote_item_is_reactivated(self):
        item = create_catalog_item(remote_id="10")
        item.is_active = False
        item.remote_deleted_at = item.updated_at
        item.save(update_fields=("is_active", "remote_deleted_at"))

        TocOnlineCatalogSynchronizer(FakeCatalogClient(services=[remote_item("10")])).synchronize()

        item.refresh_from_db()
        self.assertTrue(item.is_active)
        self.assertIsNone(item.remote_deleted_at)

    def test_empty_remote_catalog_never_deactivates_local_items(self):
        item = create_catalog_item()

        with self.assertRaises(CatalogSyncError):
            TocOnlineCatalogSynchronizer(FakeCatalogClient()).synchronize()

        item.refresh_from_db()
        self.assertTrue(item.is_active)

    def test_booking_snapshot_survives_remote_update_and_deactivation(self):
        account = create_account()
        item = create_catalog_item(price="20.00")
        booking, _ = create_booking(account, stay_item=item, duration=2)
        original_total = booking.total_price_eur

        TocOnlineCatalogSynchronizer(
            FakeCatalogClient(products=[remote_item("another-product")])
        ).synchronize()

        item.refresh_from_db()
        booking.refresh_from_db()
        self.assertFalse(item.is_active)
        self.assertEqual(booking.total_price_eur, original_total)
        self.assertEqual(booking.total_price_eur, Decimal("40.00"))

    def test_product_and_service_with_same_remote_id_are_distinct(self):
        client = FakeCatalogClient(
            services=[remote_item("42", name="Service")],
            products=[remote_item("42", name="Product")],
        )

        TocOnlineCatalogSynchronizer(client).synchronize()

        self.assertTrue(
            CatalogItem.objects.filter(
                toconline_id="42",
                toconline_type=CatalogItemType.SERVICE,
            ).exists()
        )
        self.assertTrue(
            CatalogItem.objects.filter(
                toconline_id="42",
                toconline_type=CatalogItemType.PRODUCT,
            ).exists()
        )

    def test_website_item_is_published_with_reserved_code(self):
        item = self.create_website_item()
        client = FakeCatalogClient()

        result = TocOnlineCatalogSynchronizer(client).synchronize()

        item.refresh_from_db()
        self.assertTrue(item.is_available)
        self.assertEqual(result.published, 1)
        self.assertEqual(item.toconline_id, "published-1")
        self.assertEqual(item.sync_status, CatalogSyncStatus.SYNCED)
        self.assertEqual(client.created[0][1]["item_code"], "OC-S-00000001")

    def test_toconline_failure_does_not_disable_website_item(self):
        item = self.create_website_item()

        result = TocOnlineCatalogSynchronizer(FakeCatalogClient(fail_create=True)).synchronize()

        item.refresh_from_db()
        self.assertEqual(result.published, 0)
        self.assertTrue(item.is_available)
        self.assertEqual(item.sync_status, CatalogSyncStatus.FAILED)
        self.assertIn("RuntimeError", item.sync_error)
        self.assertIsNotNone(item.sync_attempted_at)

    def test_existing_remote_code_recovers_after_lost_create_response(self):
        item = self.create_website_item()
        client = FakeCatalogClient(
            services=[
                remote_item(
                    "recovered-1",
                    name=item.name,
                    gross_price="49.90",
                    item_code=item.item_code,
                )
            ]
        )

        result = TocOnlineCatalogSynchronizer(client).synchronize()

        item.refresh_from_db()
        self.assertEqual(result.published, 0)
        self.assertEqual(item.toconline_id, "recovered-1")
        self.assertEqual(item.sync_status, CatalogSyncStatus.SYNCED)
        self.assertEqual(client.created, [])

    def test_remote_divergence_never_overwrites_website_commercial_data(self):
        item = self.create_website_item(
            toconline_id="remote-1",
            sync_status=CatalogSyncStatus.SYNCED,
        )
        client = FakeCatalogClient(
            services=[
                remote_item(
                    "remote-1",
                    name="Manually changed in ERP",
                    gross_price="99.00",
                    item_code=item.item_code,
                )
            ]
        )

        result = TocOnlineCatalogSynchronizer(client).synchronize()

        item.refresh_from_db()
        self.assertEqual(result.diverged, 1)
        self.assertEqual(item.name, "ATL Gold")
        self.assertEqual(item.unit_price_eur, Decimal("49.90"))
        self.assertEqual(item.sync_status, CatalogSyncStatus.DIVERGED)
