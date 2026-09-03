from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from activities.models import OfferingMode
from catalog.models import (
    AddOnAvailability,
    CatalogLevel,
    CatalogOffer,
    CatalogType,
    CatalogWorkflow,
)


class InvalidOptionalServiceConfiguration(ValueError):
    """Raised when an optional-service assignment is outside its catalog scope."""


@dataclass(frozen=True)
class OptionalServiceConfigurationResult:
    activated: int
    deactivated: int


def optional_service_offers():
    """Return offers that can safely be attached to another catalog service."""

    return (
        CatalogOffer.objects.select_related(
            "catalog_item",
            "catalog_type",
            "level",
            "activity_offering",
        )
        .filter(
            catalog_type__workflow=CatalogWorkflow.FLEXIBLE,
            activity_offering__mode=OfferingMode.ONE_OFF,
        )
        .order_by("catalog_type__order", "level__order", "order", "name", "pk")
    )


@transaction.atomic
def replace_optional_services(
    *,
    target_type: CatalogType,
    target_level: CatalogLevel | None,
    optional_service_ids: list[int],
    actor,
) -> OptionalServiceConfigurationResult:
    """Replace direct assignments for one target without deleting configuration history."""

    locked_type = CatalogType.objects.select_for_update().get(pk=target_type.pk)
    locked_level = None
    if target_level is not None:
        locked_level = (
            CatalogLevel.objects.select_for_update()
            .filter(
                pk=target_level.pk,
                catalog_type=locked_type,
            )
            .first()
        )
        if locked_level is None:
            raise InvalidOptionalServiceConfiguration(
                "The selected level does not belong to the target catalog type."
            )

    requested_ids = set(optional_service_ids)
    if len(requested_ids) != len(optional_service_ids):
        raise InvalidOptionalServiceConfiguration("Optional service identifiers must be unique.")

    eligible_ids = set(
        optional_service_offers()
        .filter(pk__in=requested_ids)
        .exclude(catalog_type=locked_type)
        .values_list("pk", flat=True)
    )
    if eligible_ids != requested_ids:
        raise InvalidOptionalServiceConfiguration(
            "One or more selected offers are not optional one-off services."
        )

    if locked_level is not None:
        inherited_ids = set(
            AddOnAvailability.objects.filter(
                target_type=locked_type,
                target_level__isnull=True,
                is_active=True,
            ).values_list("add_on_id", flat=True)
        )
        if requested_ids & inherited_ids:
            raise InvalidOptionalServiceConfiguration(
                "An inherited service must be managed at catalog-type level."
            )

    direct_assignments = list(
        AddOnAvailability.objects.select_for_update().filter(
            target_type=locked_type,
            target_level=locked_level,
        )
    )
    assignments_by_offer = {assignment.add_on_id: assignment for assignment in direct_assignments}
    changed_at = timezone.now()
    to_create = []
    to_update = []
    activated = 0
    deactivated = 0

    for optional_service_id in requested_ids:
        assignment = assignments_by_offer.get(optional_service_id)
        if assignment is None:
            to_create.append(
                AddOnAvailability(
                    add_on_id=optional_service_id,
                    target_type=locked_type,
                    target_level=locked_level,
                    created_by=actor,
                    is_active=True,
                )
            )
            activated += 1
        elif not assignment.is_active:
            assignment.is_active = True
            assignment.updated_at = changed_at
            to_update.append(assignment)
            activated += 1

    for assignment in direct_assignments:
        if assignment.is_active and assignment.add_on_id not in requested_ids:
            assignment.is_active = False
            assignment.updated_at = changed_at
            to_update.append(assignment)
            deactivated += 1

    if to_create:
        AddOnAvailability.objects.bulk_create(to_create)
    if to_update:
        AddOnAvailability.objects.bulk_update(to_update, ("is_active", "updated_at"))

    return OptionalServiceConfigurationResult(
        activated=activated,
        deactivated=deactivated,
    )
