from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext as _

from activities.models import (
    SUBSCRIPTION_EXCLUSIVE_STATUSES,
    ActivityReservation,
    OfferingMode,
    ReservationStatus,
    ServiceEntitlement,
    ServiceOffering,
)
from catalog.models import CatalogWorkflow


class ActivityAddOnError(ValidationError):
    """A safe business validation error for an optional activity service."""


def available_activity_add_ons(reservation):
    """Return buyable one-off services configured for this reservation context.

    Availability is based only on stable catalog relationships. Commercial
    names and slugs never affect eligibility.
    """

    level_reference = reservation.entitlement.catalog_level_reference
    eligible_target = Q(catalog_offer__add_on_availabilities__target_level__isnull=True)
    if level_reference:
        eligible_target |= Q(catalog_offer__add_on_availabilities__target_level_id=level_reference)

    return (
        ServiceOffering.objects.select_related(
            "catalog_offer__catalog_item",
            "catalog_offer__catalog_type",
            "catalog_offer__level",
        )
        .filter(
            eligible_target,
            mode=OfferingMode.ONE_OFF,
            is_published=True,
            catalog_offer__is_active=True,
            catalog_offer__catalog_type__workflow=CatalogWorkflow.FLEXIBLE,
            catalog_offer__catalog_type__is_active=True,
            catalog_offer__catalog_item__is_active=True,
            catalog_offer__add_on_availabilities__target_type_id=(
                reservation.session.catalog_type_id
            ),
            catalog_offer__add_on_availabilities__is_active=True,
        )
        .filter(
            Q(catalog_offer__catalog_item__origin="website")
            | Q(
                catalog_offer__catalog_item__remote_deleted_at__isnull=True,
                catalog_offer__catalog_item__toconline_id__isnull=False,
            )
        )
        .distinct()
    )


def validate_activity_add_on_purchase(
    *,
    reservation,
    offering,
    customer,
    exclude_entitlement_id=None,
):
    """Validate ownership, reservation state, catalog relation and duplication."""

    if reservation.entitlement.customer_id != customer.pk:
        raise ActivityAddOnError(_("The activity reservation belongs to another customer."))
    if reservation.status != ReservationStatus.BOOKED:
        raise ActivityAddOnError(_("Optional services require an active activity reservation."))
    if not available_activity_add_ons(reservation).filter(pk=offering.pk).exists():
        raise ActivityAddOnError(
            _("This optional service is not available for the reserved activity.")
        )
    duplicate = ServiceEntitlement.objects.filter(
        source_activity_reservation=reservation,
        offering=offering,
        status__in=SUBSCRIPTION_EXCLUSIVE_STATUSES,
    )
    if exclude_entitlement_id:
        duplicate = duplicate.exclude(pk=exclude_entitlement_id)
    if duplicate.exists():
        raise ActivityAddOnError(
            _("This optional service is already attached to the activity reservation.")
        )


def activity_reservation_with_purchase_context(reservation_id):
    """Load all relations used by validation and checkout without hidden queries."""

    return ActivityReservation.objects.select_related(
        "session__catalog_type",
        "entitlement__customer__user",
        "entitlement__offering__catalog_offer__level",
        "pet",
    ).get(pk=reservation_id)
