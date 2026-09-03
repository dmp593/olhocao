from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from activities.models import ServiceEntitlement
from discounts.models import DiscountSource, PurchaseKind, SaleChannel
from discounts.services import DiscountLine, quote_promotion, reserve_promotion


def activity_purchase_reference(entitlement):
    return f"activity-entitlement:{entitlement.pk}"


def offering_discount_line(offering, pet, *, source_activity_reservation=None):
    """Build the discount context for a direct purchase or a reservation add-on."""

    offer = offering.catalog_offer
    sale_channel = SaleChannel(offering.mode)
    host_catalog_type_id = None
    service_start = None
    service_end = None
    if source_activity_reservation is not None:
        sale_channel = SaleChannel.ADD_ON
        host_catalog_type_id = source_activity_reservation.session.catalog_type_id
        service_start = timezone.localtime(source_activity_reservation.session.starts_at).date()
        # Discount date ranges use an exclusive upper bound. An activity
        # reservation represents one service day even when its end time falls
        # on that same calendar day.
        service_end = service_start + timedelta(days=1)
    return DiscountLine(
        key="activity",
        amount_eur=offering.catalog_item.unit_price_eur,
        sale_channel=sale_channel,
        catalog_type_id=offer.catalog_type_id,
        catalog_level_id=offer.level_id,
        catalog_offer_id=offer.pk,
        host_catalog_type_id=host_catalog_type_id,
        pet_id=pet.pk,
        breed=pet.breed,
        service_start=service_start,
        service_end=service_end,
    )


def preview_activity_promotion(*, offering, pet, customer, code, source_activity_reservation=None):
    return quote_promotion(
        code,
        customer=customer,
        lines=(
            offering_discount_line(
                offering,
                pet,
                source_activity_reservation=source_activity_reservation,
            ),
        ),
    )


@transaction.atomic
def apply_promotion_to_entitlement(entitlement, code, *, actor=None):
    locked = (
        ServiceEntitlement.objects.select_for_update()
        .select_related(
            "customer",
            "pet",
            "offering__catalog_offer__catalog_item",
            "source_activity_reservation__session",
        )
        .get(pk=entitlement.pk)
    )
    quote, _redemption = reserve_promotion(
        code,
        customer=locked.customer,
        lines=(
            offering_discount_line(
                locked.offering,
                locked.pet,
                source_activity_reservation=locked.source_activity_reservation,
            ),
        ),
        purchase_kind=PurchaseKind.ACTIVITY_ENTITLEMENT,
        purchase_reference=activity_purchase_reference(locked),
    )
    promotion = quote.promotion
    locked.discount_source = DiscountSource.PROMOTION
    locked.promotion = promotion
    locked.promotion_code_snapshot = promotion.code
    locked.promotion_name_snapshot = promotion.name
    locked.promotion_discount_type_snapshot = promotion.discount_type
    locked.promotion_value_snapshot = promotion.value
    locked.discount_amount_eur_snapshot = quote.discount_amount_eur
    locked.discount_reason_snapshot = ""
    locked.discount_applied_by = actor
    locked.discount_applied_at = timezone.now()
    locked.save(
        update_fields=(
            "discount_source",
            "promotion",
            "promotion_code_snapshot",
            "promotion_name_snapshot",
            "promotion_discount_type_snapshot",
            "promotion_value_snapshot",
            "discount_amount_eur_snapshot",
            "discount_reason_snapshot",
            "discount_applied_by",
            "discount_applied_at",
            "updated_at",
        )
    )
    return quote
