import hashlib
import json
import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from hotel.models import (
    CatalogItem,
    CatalogItemOrigin,
    CatalogItemType,
    CatalogSyncStatus,
)
from olhocao.integrations.toconline import toconline_gateway

logger = logging.getLogger(__name__)


class CatalogSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class CatalogSyncResult:
    created: int
    updated: int
    deactivated: int
    published: int = 0
    diverged: int = 0


class TocOnlineCatalogSynchronizer:
    """Publish the website catalog and safely reconcile older TOConline imports."""

    def __init__(self, client=toconline_gateway):
        self.client = client

    def synchronize(self) -> CatalogSyncResult:
        families = self.client.list_item_families()
        services = self.client.list_services()
        products = self.client.list_products()
        if (
            not services
            and not products
            and CatalogItem.objects.exclude(toconline_id__isnull=True).exists()
        ):
            raise CatalogSyncError(
                "TOConline returned an empty catalog; existing mappings were left untouched."
            )

        family_names = {
            str(family["id"]): family.get("attributes", {}).get("name", "")
            for family in families
            if family.get("id") is not None
        }
        now = timezone.now()
        created = 0
        updated = 0
        diverged = 0
        seen_keys = set()

        with transaction.atomic():
            for item_type, remote_items in (
                (CatalogItemType.SERVICE, services),
                (CatalogItemType.PRODUCT, products),
            ):
                for remote_item in remote_items:
                    remote_values = self._catalog_values(
                        remote_item,
                        item_type=item_type,
                        family_names=family_names,
                        seen_at=now,
                    )
                    remote_id = remote_values.pop("toconline_id")
                    key = (item_type, remote_id)
                    seen_keys.add(key)
                    item = CatalogItem.objects.filter(
                        toconline_type=item_type,
                        toconline_id=remote_id,
                    ).first()
                    if item is None:
                        item = CatalogItem.objects.filter(
                            origin=CatalogItemOrigin.WEBSITE,
                            toconline_type=item_type,
                            toconline_id__isnull=True,
                            item_code=remote_values["item_code"],
                        ).first()
                    if item is None:
                        CatalogItem.objects.create(
                            toconline_id=remote_id,
                            toconline_type=item_type,
                            origin=CatalogItemOrigin.TOCONLINE,
                            sync_status=CatalogSyncStatus.SYNCED,
                            remote_fingerprint=self._remote_fingerprint(
                                item_type,
                                remote_values,
                            ),
                            synced_at=now,
                            **remote_values,
                        )
                        created += 1
                        continue

                    if item.origin == CatalogItemOrigin.WEBSITE:
                        item.toconline_id = remote_id
                        item.last_seen_at = now
                        item.remote_deleted_at = None
                        remote_fingerprint = self._remote_fingerprint(item_type, remote_values)
                        item.remote_fingerprint = remote_fingerprint
                        if remote_fingerprint == self._local_fingerprint(item):
                            item.sync_status = CatalogSyncStatus.SYNCED
                            item.sync_error = ""
                            item.synced_at = now
                        else:
                            item.sync_status = CatalogSyncStatus.DIVERGED
                            item.sync_error = (
                                "TOConline commercial data differs from the website version."
                            )
                            diverged += 1
                        item.save()
                        continue

                    for field_name, value in remote_values.items():
                        setattr(item, field_name, value)
                    item.remote_fingerprint = self._remote_fingerprint(item_type, remote_values)
                    item.sync_status = CatalogSyncStatus.SYNCED
                    item.sync_error = ""
                    item.synced_at = now
                    item.save()
                    updated += 1

            deactivated = self._mark_missing_remote_items(seen_keys, now)

        published = 0
        pending_items = CatalogItem.objects.filter(
            origin=CatalogItemOrigin.WEBSITE,
            toconline_id__isnull=True,
            sync_status__in=(CatalogSyncStatus.PENDING, CatalogSyncStatus.FAILED),
        )
        for item in pending_items.iterator():
            try:
                published_item = self.publish_item(item)
            except Exception:
                logger.exception(
                    "TOConline catalog publication remains pending",
                    extra={"catalog_item_id": item.pk},
                )
                continue
            if published_item.sync_status == CatalogSyncStatus.SYNCED:
                published += 1

        return CatalogSyncResult(
            created=created,
            updated=updated,
            deactivated=deactivated,
            published=published,
            diverged=diverged,
        )

    def publish_item(self, item):
        item = CatalogItem.objects.get(pk=item.pk)
        if item.origin != CatalogItemOrigin.WEBSITE:
            raise CatalogSyncError("Only website-managed catalog items can be published.")
        if item.toconline_id:
            return item
        if not item.item_code.startswith("OC-"):
            raise CatalogSyncError("Website-managed catalog items require a reserved OC code.")

        attempted_at = timezone.now()
        try:
            with transaction.atomic():
                locked = CatalogItem.objects.select_for_update().get(pk=item.pk)
                if locked.toconline_id:
                    return locked

                locked.sync_attempted_at = attempted_at
                locked.save(update_fields=("sync_attempted_at", "updated_at"))

                resource = self.client.find_catalog_item(
                    locked.toconline_type,
                    item_code=locked.item_code,
                )
                if resource is None:
                    resource = self.client.create_catalog_item(
                        locked.toconline_type,
                        item_code=locked.item_code,
                        item_description=locked.name,
                        sales_price=float(locked.unit_price_eur),
                        sales_price_includes_vat=True,
                        tax_code=locked.tax_code,
                    )

                now = timezone.now()
                attributes = resource.get("attributes") or {}
                locked.toconline_id = str(resource["id"])
                remote_fingerprint = self._remote_fingerprint(
                    locked.toconline_type,
                    {
                        "item_code": str(attributes.get("item_code") or locked.item_code),
                        "name": str(attributes.get("item_description") or locked.name),
                        "unit_price_eur": self._payable_price(
                            attributes
                            or {
                                "sales_price": locked.unit_price_eur,
                                "sales_price_includes_vat": True,
                            },
                            str(resource["id"]),
                        ),
                        "tax_code": str(
                            attributes.get("applied_tax_code")
                            or attributes.get("tax_code")
                            or locked.tax_code
                        ),
                    },
                )
                locked.remote_fingerprint = remote_fingerprint
                locked.sync_attempted_at = attempted_at
                locked.last_seen_at = now
                locked.remote_deleted_at = None
                if remote_fingerprint == self._local_fingerprint(locked):
                    locked.sync_status = CatalogSyncStatus.SYNCED
                    locked.sync_error = ""
                    locked.synced_at = now
                else:
                    locked.sync_status = CatalogSyncStatus.DIVERGED
                    locked.sync_error = (
                        "An item with this reserved code exists in TOConline "
                        "but its commercial data differs."
                    )
                locked.save()
        except Exception as exc:
            CatalogItem.objects.filter(pk=item.pk).update(
                sync_status=CatalogSyncStatus.FAILED,
                sync_error=f"{type(exc).__name__}: {str(exc)[:500]}",
                sync_attempted_at=attempted_at,
            )
            raise
        return locked

    @staticmethod
    def managed_item_code(item):
        type_marker = "S" if item.toconline_type == CatalogItemType.SERVICE else "P"
        return f"OC-{type_marker}-{item.pk:08d}"

    def _mark_missing_remote_items(self, seen_keys, now):
        missing_count = 0
        for item in CatalogItem.objects.exclude(toconline_id__isnull=True):
            if (item.toconline_type, item.toconline_id) in seen_keys:
                continue
            item.remote_deleted_at = now
            item.sync_status = CatalogSyncStatus.DIVERGED
            if item.origin == CatalogItemOrigin.WEBSITE:
                item.sync_error = "The website-managed item is missing from TOConline."
            else:
                item.is_active = False
                item.sync_error = "The imported item is no longer available in TOConline."
            item.save()
            missing_count += 1
        return missing_count

    def _catalog_values(self, remote_item, *, item_type, family_names, seen_at):
        remote_id = str(remote_item.get("id", "")).strip()
        if not remote_id:
            raise CatalogSyncError("TOConline returned an item without an id.")

        attributes = remote_item.get("attributes") or {}
        name = str(attributes.get("item_description") or "").strip()
        if not name:
            raise CatalogSyncError(f"TOConline item {remote_id} has no item_description.")
        family_id = self._family_id(remote_item)
        return {
            "toconline_id": remote_id,
            "item_code": str(attributes.get("item_code") or "").strip(),
            "name": name,
            "description": str(attributes.get("notes") or "").strip(),
            "family_id": family_id,
            "family_name": family_names.get(family_id, ""),
            "unit_price_eur": self._payable_price(attributes, remote_id),
            "tax_code": str(
                attributes.get("applied_tax_code") or attributes.get("tax_code") or ""
            ).strip(),
            "currency": "EUR",
            "is_active": bool(attributes.get("is_active", True)),
            "last_seen_at": seen_at,
            "remote_deleted_at": None,
        }

    @staticmethod
    def _family_id(remote_item):
        relationship = (remote_item.get("relationships") or {}).get("item_families", {}).get("data")
        if isinstance(relationship, list):
            relationship = relationship[0] if relationship else None
        if not relationship:
            return ""
        return str(relationship.get("id") or "")

    @staticmethod
    def _payable_price(attributes, remote_id):
        price = attributes.get("sales_price")
        if not attributes.get("sales_price_includes_vat"):
            price = attributes.get("sales_price_vat_display") or price
        try:
            amount = Decimal(str(price or "0")).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
        except (InvalidOperation, TypeError) as exc:
            raise CatalogSyncError(
                f"TOConline item {remote_id} has an invalid sales_price."
            ) from exc
        if amount < 0:
            raise CatalogSyncError(f"TOConline item {remote_id} has a negative sales_price.")
        return amount

    @staticmethod
    def _fingerprint(values):
        return hashlib.sha256(
            json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _remote_fingerprint(self, item_type, values):
        return self._fingerprint(
            {
                "type": item_type,
                "item_code": str(values.get("item_code") or "").strip(),
                "name": str(values.get("name") or "").strip(),
                "price": format(Decimal(values.get("unit_price_eur") or 0), ".2f"),
                "tax_code": str(values.get("tax_code") or "").strip(),
            }
        )

    def _local_fingerprint(self, item):
        return self._remote_fingerprint(
            item.toconline_type,
            {
                "item_code": item.item_code,
                "name": item.name,
                "unit_price_eur": item.unit_price_eur,
                "tax_code": item.tax_code,
            },
        )


def catalog_version_history(item):
    """Return every fiscal version connected to an item, newest first."""
    root = item
    seen_ids = set()
    while root.supersedes_id and root.pk not in seen_ids:
        seen_ids.add(root.pk)
        root = root.supersedes

    versions = []
    pending = [root]
    seen_ids.clear()
    while pending:
        version = pending.pop()
        if version.pk in seen_ids:
            continue
        seen_ids.add(version.pk)
        versions.append(version)
        pending.extend(version.newer_versions.all())

    return sorted(versions, key=lambda version: (version.created_at, version.pk), reverse=True)
