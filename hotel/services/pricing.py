from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from discounts.models import DiscountSource, PurchaseKind, SaleChannel
from discounts.services import (
    DiscountLine,
    PromotionUnavailable,
    allocate_discount,
    consume_redemption,
    money,
    quote_promotion,
    release_redemption,
    reserve_promotion,
)
from hotel.models import (
    Booking,
    BookingDocumentStatus,
    BookingEvent,
    BookingEventType,
    BookingPriceAdjustment,
    BookingStatus,
    PaymentStatus,
)
from hotel.services.payments import (
    MINIMUM_ONLINE_PAYMENT_EUR,
    EasypayClient,
    PaymentGatewayError,
)

FINAL_PAYMENT_STATUSES = (
    PaymentStatus.PAID,
    PaymentStatus.REFUND_PENDING,
    PaymentStatus.REFUND_FAILED,
    PaymentStatus.REFUNDED,
    PaymentStatus.DISPUTED,
)


class BookingPriceChangeError(ValueError):
    """A safe explanation for a rejected staff price change."""


def booking_purchase_reference(booking):
    return f"hotel-booking:{booking.pk}"


def review_discount_lines(review):
    lines = []
    for pet in review.pets:
        stay_item = review.stay.catalog_item
        lines.append(
            DiscountLine(
                key=f"stay:{pet.pk}",
                amount_eur=stay_item.unit_price_eur * review.duration,
                sale_channel=SaleChannel.DATE_RANGE,
                catalog_type_id=review.stay.catalog_type_id,
                catalog_level_id=review.stay.level_id,
                catalog_offer_id=review.stay.pk,
                pet_id=pet.pk,
                breed=pet.breed,
                service_start=review.start_date,
                service_end=review.end_date,
            )
        )
        for service_data in review.pets_services[str(pet.pk)]["services"]:
            service = service_data["service"]
            lines.append(
                DiscountLine(
                    key=f"service:{pet.pk}:{service.pk}",
                    amount_eur=service_data["total_price"],
                    sale_channel=SaleChannel.ADD_ON,
                    catalog_type_id=service.catalog_type_id,
                    catalog_level_id=service.level_id,
                    catalog_offer_id=service.pk,
                    host_catalog_type_id=review.stay.catalog_type_id,
                    pet_id=pet.pk,
                    breed=pet.breed,
                    service_start=review.start_date,
                    service_end=review.end_date,
                )
            )
    return tuple(lines)


def preview_booking_promotion(review, *, customer, code):
    return quote_promotion(
        code,
        customer=customer,
        lines=review_discount_lines(review),
    )


def booking_discount_lines(booking):
    lines = []
    stays = booking.stays.select_related("pet", "catalog_offer").prefetch_related(
        "services__catalog_offer"
    )
    for stay in stays:
        lines.append(
            DiscountLine(
                key=f"stay:{stay.pk}",
                amount_eur=stay.gross_accommodation_price_eur,
                sale_channel=SaleChannel.DATE_RANGE,
                catalog_type_id=stay.catalog_type_reference,
                catalog_level_id=stay.catalog_level_reference,
                catalog_offer_id=stay.catalog_offer_id,
                pet_id=stay.pet_id,
                breed=stay.pet.breed,
                service_start=stay.start_date,
                service_end=stay.end_date,
            )
        )
        for service in stay.services.all():
            lines.append(
                DiscountLine(
                    key=f"service:{service.pk}",
                    amount_eur=service.gross_total_price_eur,
                    sale_channel=SaleChannel.ADD_ON,
                    catalog_type_id=service.catalog_type_reference,
                    catalog_level_id=service.catalog_level_reference,
                    catalog_offer_id=service.catalog_offer_id,
                    host_catalog_type_id=stay.catalog_type_reference,
                    pet_id=service.pet_id,
                    breed=stay.pet.breed,
                    service_start=stay.start_date,
                    service_end=stay.end_date,
                )
            )
    return tuple(lines)


@transaction.atomic
def apply_promotion_to_booking(booking, code, *, actor=None):
    locked_booking = (
        Booking.objects.select_for_update().select_related("customer").get(pk=booking.pk)
    )
    if locked_booking.status != BookingStatus.PENDING:
        raise PromotionUnavailable(_("A promotion can only be applied before confirmation."))
    if locked_booking.payment_attempts.exists():
        raise PromotionUnavailable(
            _("The price cannot be changed after a payment link has been created.")
        )

    quote, _redemption = reserve_promotion(
        code,
        customer=locked_booking.customer,
        lines=booking_discount_lines(locked_booking),
        purchase_kind=PurchaseKind.HOTEL_BOOKING,
        purchase_reference=booking_purchase_reference(locked_booking),
    )
    if Decimal("0.00") < quote.total_amount_eur < MINIMUM_ONLINE_PAYMENT_EUR:
        raise PromotionUnavailable(
            _(
                "This promotion would reduce the booking below the minimum online "
                "payment amount. Please contact the team."
            )
        )
    _write_line_allocations(locked_booking, quote.allocations)
    _write_promotion_snapshot(locked_booking, quote, actor=actor)

    if quote.total_amount_eur == Decimal("0.00"):
        _confirm_complimentary_booking(locked_booking, actor=actor)
        consume_redemption(booking_purchase_reference(locked_booking))
    return quote


@transaction.atomic
def set_booking_final_price(
    booking,
    *,
    final_price_eur,
    reason,
    actor,
    payment_client=None,
):
    """Replace the pending booking discount with an audited staff adjustment."""

    if actor is None or not actor.is_staff:
        raise BookingPriceChangeError(_("Only staff can change a booking price."))
    reason = " ".join(str(reason or "").split())
    if len(reason) < 5:
        raise BookingPriceChangeError(_("Explain why this price is being changed."))

    locked_booking = (
        Booking.objects.select_for_update().select_related("customer").get(pk=booking.pk)
    )
    if locked_booking.status not in {BookingStatus.PENDING, BookingStatus.COMPLIMENTARY}:
        raise BookingPriceChangeError(
            _("Only unpaid or complimentary bookings can have their price changed.")
        )
    if locked_booking.toconline_sale_document_id or locked_booking.document_status == (
        BookingDocumentStatus.ISSUED
    ):
        raise BookingPriceChangeError(
            _("This price is locked because a fiscal document has already been issued.")
        )
    if locked_booking.payment_attempts.filter(status__in=FINAL_PAYMENT_STATUSES).exists():
        raise BookingPriceChangeError(
            _("This price is locked because a payment has already been captured.")
        )

    gross_amount = money(locked_booking.gross_total_price_eur)
    final_price = money(final_price_eur)
    previous_total = money(locked_booking.total_price_eur)
    if final_price < Decimal("0.00") or final_price > gross_amount:
        raise BookingPriceChangeError(
            _("The final price must be between 0.00 € and %(amount).2f €.")
            % {"amount": gross_amount}
        )
    if Decimal("0.00") < final_price < MINIMUM_ONLINE_PAYMENT_EUR:
        raise BookingPriceChangeError(
            _(
                "Choose 0.00 € for a complimentary booking or at least %(amount).2f € "
                "for online payment."
            )
            % {"amount": MINIMUM_ONLINE_PAYMENT_EUR}
        )
    if final_price == previous_total:
        raise BookingPriceChangeError(_("Choose a different final price."))

    _disable_existing_payment_links(locked_booking, payment_client=payment_client)
    release_redemption(booking_purchase_reference(locked_booking))

    discount_amount = money(gross_amount - final_price)
    allocations = allocate_discount(booking_discount_lines(locked_booking), discount_amount)
    _write_line_allocations(locked_booking, allocations)
    _write_staff_adjustment_snapshot(
        locked_booking,
        discount_amount=discount_amount,
        reason=reason,
        actor=actor,
    )

    if final_price == Decimal("0.00"):
        _confirm_complimentary_booking(locked_booking, actor=actor)
    else:
        locked_booking.status = BookingStatus.PENDING
        locked_booking.confirmed_at = None
        locked_booking.paid_at = None
        locked_booking.document_status = BookingDocumentStatus.NOT_REQUIRED
        locked_booking.document_error = ""
        locked_booking.save(
            update_fields=(
                "status",
                "confirmed_at",
                "paid_at",
                "document_status",
                "document_error",
                "updated_at",
            )
        )

    BookingPriceAdjustment.objects.create(
        booking=locked_booking,
        actor=actor,
        gross_amount_eur=gross_amount,
        previous_total_amount_eur=previous_total,
        new_total_amount_eur=final_price,
        discount_amount_eur=discount_amount,
        reason=reason,
    )
    BookingEvent.objects.create(
        booking=locked_booking,
        event_type=BookingEventType.PRICE_ADJUSTED,
        actor=actor,
        details={
            "gross_amount_eur": str(gross_amount),
            "previous_total_amount_eur": str(previous_total),
            "new_total_amount_eur": str(final_price),
            "discount_amount_eur": str(discount_amount),
            "reason": reason,
        },
    )
    return locked_booking


def _disable_existing_payment_links(booking, *, payment_client=None):
    payment_client = payment_client or EasypayClient()
    attempts = tuple(
        booking.payment_attempts.select_for_update().filter(
            status__in=(PaymentStatus.PENDING, PaymentStatus.FAILED)
        )
    )
    for attempt in attempts:
        if attempt.provider_link_id:
            try:
                payment_client.cancel_payment_link(attempt.provider_link_id)
            except PaymentGatewayError as exc:
                raise BookingPriceChangeError(
                    _(
                        "The existing payment link could not be disabled, so the price "
                        "was not changed. Please try again."
                    )
                ) from exc
        attempt.status = PaymentStatus.CANCELLED
        attempt.last_error = _("Payment link disabled because staff changed the booking price.")
        attempt.save(update_fields=("status", "last_error", "updated_at"))


def _write_line_allocations(booking, allocations):
    stays = list(booking.stays.prefetch_related("services"))
    services = []
    for stay in stays:
        stay.discount_amount_eur_snapshot = allocations.get(f"stay:{stay.pk}", Decimal("0.00"))
        for service in stay.services.all():
            service.discount_amount_eur_snapshot = allocations.get(
                f"service:{service.pk}", Decimal("0.00")
            )
            services.append(service)
    if stays:
        type(stays[0]).objects.bulk_update(stays, ("discount_amount_eur_snapshot",))
    if services:
        type(services[0]).objects.bulk_update(services, ("discount_amount_eur_snapshot",))


def _write_promotion_snapshot(booking, quote, *, actor):
    promotion = quote.promotion
    booking.discount_source = DiscountSource.PROMOTION
    booking.promotion = promotion
    booking.promotion_code_snapshot = promotion.code
    booking.promotion_name_snapshot = promotion.name
    booking.promotion_discount_type_snapshot = promotion.discount_type
    booking.promotion_value_snapshot = promotion.value
    booking.discount_amount_eur_snapshot = quote.discount_amount_eur
    booking.discount_reason_snapshot = ""
    booking.discount_applied_by = actor
    booking.discount_applied_at = timezone.now()
    booking.save(
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


def _write_staff_adjustment_snapshot(booking, *, discount_amount, reason, actor):
    booking.discount_source = (
        DiscountSource.STAFF if discount_amount > Decimal("0.00") else DiscountSource.NONE
    )
    booking.promotion = None
    booking.promotion_code_snapshot = ""
    booking.promotion_name_snapshot = ""
    booking.promotion_discount_type_snapshot = ""
    booking.promotion_value_snapshot = None
    booking.discount_amount_eur_snapshot = discount_amount
    booking.discount_reason_snapshot = reason if discount_amount else ""
    booking.discount_applied_by = actor if discount_amount else None
    booking.discount_applied_at = timezone.now() if discount_amount else None
    booking.save(
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


def _confirm_complimentary_booking(booking, *, actor):
    booking.status = BookingStatus.COMPLIMENTARY
    booking.confirmed_at = booking.confirmed_at or timezone.now()
    booking.paid_at = None
    booking.document_status = BookingDocumentStatus.NOT_REQUIRED
    booking.document_error = ""
    booking.save(
        update_fields=(
            "status",
            "confirmed_at",
            "paid_at",
            "document_status",
            "document_error",
            "updated_at",
        )
    )
    BookingEvent.objects.get_or_create(
        booking=booking,
        event_type=BookingEventType.COMPLIMENTARY_CONFIRMED,
        defaults={
            "actor": actor,
            "details": {"reason": booking.discount_reason_snapshot},
        },
    )
    from activities.services.fulfilments import activate_confirmed_booking_fulfilments

    activate_confirmed_booking_fulfilments(booking)
