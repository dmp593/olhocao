from django.db import transaction
from django.utils import timezone

from catalog.models import CatalogLevel, CatalogType


class InvalidCatalogLevelOrder(ValueError):
    """Raised when a requested order does not match a catalog type's levels."""


@transaction.atomic
def reorder_catalog_levels(
    *,
    catalog_type: CatalogType,
    ordered_level_ids: list[int],
) -> None:
    """Persist a complete, duplicate-free order for one catalog type."""

    levels = list(CatalogLevel.objects.select_for_update().filter(catalog_type=catalog_type))
    current_level_ids = {level.pk for level in levels}

    if len(ordered_level_ids) != len(set(ordered_level_ids)):
        raise InvalidCatalogLevelOrder("Catalog level identifiers must be unique.")
    if set(ordered_level_ids) != current_level_ids:
        raise InvalidCatalogLevelOrder(
            "The order must contain every level from this catalog type and no others."
        )

    levels_by_id = {level.pk: level for level in levels}
    changed_levels = []
    changed_at = timezone.now()
    for order, level_id in enumerate(ordered_level_ids):
        level = levels_by_id[level_id]
        if level.order == order:
            continue
        level.order = order
        level.updated_at = changed_at
        changed_levels.append(level)

    if changed_levels:
        CatalogLevel.objects.bulk_update(changed_levels, ("order", "updated_at"))
