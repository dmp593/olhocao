import logging
from dataclasses import dataclass
from datetime import date, time, timedelta
from decimal import ROUND_DOWN, Decimal

from django.conf import settings
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from activities.models import OfferingMode
from catalog.models import AddOnAvailability, CatalogOffer, CatalogWorkflow
from hotel.models import (
    CONFIRMED_BOOKING_STATUSES,
    Booking,
    BookingEvent,
    BookingEventType,
    BookingService,
    BookingStatus,
    BookingStay,
    CatalogItemOrigin,
    PaymentStatus,
    ServiceSchedulePreference,
)
from hotel.services.payments import (
    EasypayClient,
    PaymentGatewayError,
)
from hotel.services.refunds import request_booking_refund
from pets.models import Pet

logger = logging.getLogger(__name__)


class BookingValidationError(ValueError):
    pass


@dataclass(frozen=True)
class BookingReview:
    stay: CatalogOffer
    pets: tuple
    pets_services: dict
    pricing: dict
    start_date: date
    end_date: date
    duration: int


@dataclass(frozen=True)
class BookingCancellationResult:
    refund_requested: bool
    refund_error: str = ""


def available_stays():
    return _available_catalog_offers().filter(
        catalog_type__workflow=CatalogWorkflow.DATE_RANGE,
    )


def available_extras(stay_offer):
    """Return reusable offers explicitly enabled for this accommodation."""

    availability = AddOnAvailability.objects.filter(
        add_on_id=models.OuterRef("pk"),
        target_type_id=stay_offer.catalog_type_id,
        is_active=True,
    ).filter(Q(target_level__isnull=True) | Q(target_level_id=stay_offer.level_id))
    return _available_catalog_offers().filter(
        models.Exists(availability),
        catalog_type__workflow=CatalogWorkflow.FLEXIBLE,
        activity_offering__mode=OfferingMode.ONE_OFF,
        activity_offering__is_published=True,
    )


def _available_catalog_offers():
    return (
        CatalogOffer.objects.select_related("catalog_item", "catalog_type", "level")
        .filter(
            is_active=True,
            is_public=True,
            catalog_type__is_active=True,
            catalog_item__is_active=True,
        )
        .filter(
            Q(catalog_item__origin=CatalogItemOrigin.WEBSITE)
            | Q(
                catalog_item__remote_deleted_at__isnull=True,
                catalog_item__toconline_id__isnull=False,
            )
        )
    )


def build_booking_review(customer, booking_data):
    start_date, end_date, duration = _validated_dates(booking_data)
    stay = _available_offer(
        booking_data.get("offer_id"),
        workflow=CatalogWorkflow.DATE_RANGE,
    )
    pets = tuple(
        Pet.objects.filter(
            id__in=booking_data.get("pet_ids", ()),
            owner=customer,
            deleted_at__isnull=True,
        )
    )
    requested_pet_ids = {str(value) for value in booking_data.get("pet_ids", ())}
    if not pets or {str(pet.pk) for pet in pets} != requested_pet_ids:
        raise BookingValidationError(_("One or more selected pets are unavailable."))

    requested_services = booking_data.get("pets_services", {})
    service_ids = {
        str(service_id) for services in requested_services.values() for service_id in services
    }
    services = {
        str(service.pk): service for service in available_extras(stay).filter(pk__in=service_ids)
    }
    if set(services) != service_ids:
        raise BookingValidationError(_("One or more selected services are unavailable."))

    pets_services = {}
    pricing_services = {}
    grand_total = stay.catalog_item.unit_price_eur * duration * len(pets)

    for pet in pets:
        pet_key = str(pet.pk)
        pet_services = []
        for service_id, raw_selection in requested_services.get(pet_key, {}).items():
            selection = _validated_service_selection(
                raw_selection,
                start_date=start_date,
                end_date=end_date,
            )
            quantity = selection["quantity"]
            if quantity < 1 or quantity > duration:
                raise BookingValidationError(
                    _("Service quantities must fit within the booking duration.")
                )
            service = services[str(service_id)]
            total_price = service.catalog_item.unit_price_eur * quantity
            pet_services.append(
                {
                    "service": service,
                    "quantity": quantity,
                    "unit_price": service.catalog_item.unit_price_eur,
                    "total_price": total_price,
                    "schedule_preference": selection["schedule_preference"],
                    "preferred_date": selection["preferred_date"],
                    "preferred_time": selection["preferred_time"],
                }
            )

            aggregate = pricing_services.setdefault(
                str(service.pk),
                {"service": service, "quantity": 0, "total_price": Decimal("0")},
            )
            aggregate["quantity"] += quantity
            aggregate["total_price"] += total_price
            grand_total += total_price

        pets_services[pet_key] = {"pet": pet, "services": pet_services}

    return BookingReview(
        stay=stay,
        pets=pets,
        pets_services=pets_services,
        pricing={
            "stay": {
                "unit_price": stay.catalog_item.unit_price_eur,
                "total_price": stay.catalog_item.unit_price_eur * duration * len(pets),
            },
            "services": pricing_services,
            "grand_total": grand_total,
        },
        start_date=start_date,
        end_date=end_date,
        duration=duration,
    )


@transaction.atomic
def create_booking(customer, booking_data, *, notes="", actor=None, promotion_code=""):
    review = build_booking_review(customer, booking_data)
    locked_offer = (
        CatalogOffer.objects.select_for_update()
        .select_related("catalog_type", "level")
        .get(pk=review.stay.pk)
    )
    ensure_stay_capacity_available(
        locked_offer,
        start_date=review.start_date,
        end_date=review.end_date,
        requested_dogs=len(review.pets),
    )
    booking = Booking.objects.create(
        customer=customer,
        created_by=actor,
        status=BookingStatus.PENDING,
        notes=notes,
    )

    for pet in review.pets:
        fiscal_item = locked_offer.catalog_item
        stay = BookingStay(
            booking=booking,
            pet=pet,
            catalog_item=fiscal_item,
            catalog_offer=locked_offer,
            catalog_type_reference=locked_offer.catalog_type_id,
            catalog_type_name_snapshot=locked_offer.catalog_type.name,
            catalog_level_reference=locked_offer.level_id,
            catalog_level_name_snapshot=(locked_offer.level.name if locked_offer.level_id else ""),
            toconline_item_id=fiscal_item.toconline_id or "",
            toconline_item_type=fiscal_item.toconline_type,
            item_code_snapshot=fiscal_item.item_code,
            tax_code_snapshot=fiscal_item.tax_code,
            item_name_snapshot=locked_offer.name,
            unit_price_eur_snapshot=fiscal_item.unit_price_eur,
            currency_snapshot=fiscal_item.currency,
            start_date=review.start_date,
            end_date=review.end_date,
        )
        stay.full_clean()
        stay.save()

        for service_data in review.pets_services[str(pet.pk)]["services"]:
            service_offer = service_data["service"]
            service_item = service_offer.catalog_item
            booking_service = BookingService(
                stay=stay,
                pet=pet,
                catalog_item=service_item,
                catalog_offer=service_offer,
                catalog_type_reference=service_offer.catalog_type_id,
                catalog_type_name_snapshot=service_offer.catalog_type.name,
                catalog_level_reference=service_offer.level_id,
                catalog_level_name_snapshot=(
                    service_offer.level.name if service_offer.level_id else ""
                ),
                toconline_item_id=service_item.toconline_id or "",
                toconline_item_type=service_item.toconline_type,
                item_code_snapshot=service_item.item_code,
                tax_code_snapshot=service_item.tax_code,
                item_name_snapshot=service_offer.name,
                unit_price_eur_snapshot=service_item.unit_price_eur,
                currency_snapshot=service_item.currency,
                quantity=service_data["quantity"],
                schedule_preference=service_data["schedule_preference"],
                preferred_date=service_data["preferred_date"],
                preferred_time=service_data["preferred_time"],
            )
            booking_service.full_clean()
            booking_service.save()
    BookingEvent.objects.create(
        booking=booking,
        event_type=BookingEventType.CREATED,
        actor=actor,
    )
    if promotion_code:
        from hotel.services.pricing import apply_promotion_to_booking

        apply_promotion_to_booking(booking, promotion_code, actor=actor)
    return booking


def cancel_booking(
    booking,
    *,
    payment_client=None,
    actor=None,
    enforce_customer_policy=True,
):
    payment_client = payment_client or EasypayClient()
    if booking.status in {BookingStatus.CANCELLED, BookingStatus.PAYMENT_DISPUTED}:
        raise BookingValidationError(_("This booking cannot be cancelled in its current state."))
    if enforce_customer_policy and not booking.can_cancel:
        raise BookingValidationError(
            _("This booking can no longer be cancelled under the current policy.")
        )

    with transaction.atomic():
        locked_booking = Booking.objects.select_for_update().get(pk=booking.pk)
        if locked_booking.status in {BookingStatus.CANCELLED, BookingStatus.PAYMENT_DISPUTED}:
            raise BookingValidationError(
                _("This booking cannot be cancelled in its current state.")
            )
        locked_booking.status = BookingStatus.CANCELLED
        locked_booking.save(update_fields=("status", "updated_at"))
        BookingEvent.objects.create(
            booking=locked_booking,
            event_type=BookingEventType.CANCELLED,
            actor=actor,
        )
        from discounts.services import release_redemption
        from hotel.services.pricing import booking_purchase_reference

        release_redemption(booking_purchase_reference(locked_booking))

    refund_requested = False
    refund_error = ""
    payment = booking.payment_attempts.filter(status=PaymentStatus.PAID).first()
    if payment:
        refund_percentage = Decimal(str(settings.HOTEL_REFUND_PERCENTAGE))
        refund_amount = (payment.amount_eur * refund_percentage).quantize(
            Decimal("0.01"),
            rounding=ROUND_DOWN,
        )
        try:
            request_booking_refund(
                payment,
                amount=refund_amount,
                actor=actor,
                reason=str(_("Booking cancellation under the configured refund policy.")),
                client=payment_client,
            )
            refund_requested = True
        except PaymentGatewayError as exc:
            refund_error = str(exc)
            logger.exception(
                "Easypay refund request failed after booking cancellation",
                extra={"booking_id": booking.pk, "payment_id": payment.pk},
            )
    else:
        payment = booking.payment_attempts.filter(status=PaymentStatus.PENDING).first()
        if payment and payment.provider_link_id:
            try:
                payment_client.cancel_payment_link(payment.provider_link_id)
            except PaymentGatewayError:
                logger.exception(
                    "Could not disable Easypay link after booking cancellation",
                    extra={"booking_id": booking.pk, "payment_id": payment.pk},
                )
            payment.status = PaymentStatus.CANCELLED
            payment.save(update_fields=("status", "updated_at"))

    booking.refresh_from_db()
    return BookingCancellationResult(
        refund_requested=refund_requested,
        refund_error=refund_error,
    )


def _available_offer(offer_id, *, workflow):
    try:
        return _available_catalog_offers().get(
            pk=offer_id,
            catalog_type__workflow=workflow,
        )
    except (CatalogOffer.DoesNotExist, TypeError, ValueError) as exc:
        raise BookingValidationError(_("The selected catalog offer is unavailable.")) from exc


def _validated_service_selection(raw_selection, *, start_date, end_date):
    if not isinstance(raw_selection, dict):
        raise BookingValidationError(_("The selected service details are invalid."))
    try:
        quantity = int(raw_selection.get("quantity", 0))
    except (TypeError, ValueError) as exc:
        raise BookingValidationError(_("The selected service quantity is invalid.")) from exc
    preference = str(
        raw_selection.get("schedule_preference") or ServiceSchedulePreference.UNSCHEDULED
    )
    if preference not in ServiceSchedulePreference.values:
        raise BookingValidationError(_("The selected scheduling preference is invalid."))
    try:
        preferred_date = (
            date.fromisoformat(str(raw_selection["preferred_date"]))
            if raw_selection.get("preferred_date")
            else None
        )
        preferred_time = (
            time.fromisoformat(str(raw_selection["preferred_time"]))
            if raw_selection.get("preferred_time")
            else None
        )
    except (TypeError, ValueError) as exc:
        raise BookingValidationError(_("The preferred service date or time is invalid.")) from exc

    if quantity < 1:
        raise BookingValidationError(_("Service quantity must be at least one."))
    if quantity > (end_date - start_date).days:
        raise BookingValidationError(_("Service quantities must fit within the booking duration."))
    if (
        preference
        in {
            ServiceSchedulePreference.DATE,
            ServiceSchedulePreference.DATE_TIME,
        }
        and preferred_date is None
    ):
        raise BookingValidationError(_("The selected service requires a preferred date."))
    if preference == ServiceSchedulePreference.DATE_TIME and preferred_time is None:
        raise BookingValidationError(_("The selected service requires a preferred time."))
    if preference != ServiceSchedulePreference.DATE_TIME and preferred_time is not None:
        raise BookingValidationError(_("A time is only allowed with a date and time preference."))
    if preference == ServiceSchedulePreference.UNSCHEDULED and preferred_date is not None:
        raise BookingValidationError(_("A date is not allowed without a date preference."))
    if preferred_date and not start_date <= preferred_date < end_date:
        raise BookingValidationError(
            _("The preferred service date must be before check-out and within the stay.")
        )
    return {
        "quantity": quantity,
        "schedule_preference": preference,
        "preferred_date": preferred_date,
        "preferred_time": preferred_time,
    }


def ensure_stay_capacity_available(
    offer,
    *,
    start_date,
    end_date,
    requested_dogs,
    exclude_booking_id=None,
):
    """Protect every night in a stay with one serialized capacity check.

    The caller holds a row lock on the offer. PostgreSQL therefore serializes
    concurrent checkouts for the same accommodation without locking unrelated
    products. Pending purchases hold capacity for the configured payment-link
    lifetime; expired attempts never block new customers.
    """

    capacity = offer.effective_capacity
    if not capacity:
        raise BookingValidationError(_("This accommodation has no capacity configured."))
    now = timezone.now()
    hold_started_at = now - timedelta(hours=settings.EASYPAY_PAYMENT_LINK_TTL_HOURS)
    active_purchase = (
        Q(booking__status__in=CONFIRMED_BOOKING_STATUSES)
        | Q(
            booking__status=BookingStatus.PENDING,
            booking__created_at__gte=hold_started_at,
        )
        | Q(
            booking__status=BookingStatus.PENDING,
            booking__payment_attempts__status=PaymentStatus.PENDING,
            booking__payment_attempts__expires_at__gt=now,
        )
    )
    stays = BookingStay.objects.filter(
        catalog_offer=offer,
        start_date__lt=end_date,
        end_date__gt=start_date,
    ).filter(active_purchase)
    if exclude_booking_id:
        stays = stays.exclude(booking_id=exclude_booking_id)

    changes_by_date = {}
    # One ``BookingStay`` represents one dog.  Equal date ranges must therefore
    # remain separate rows: collapsing them with ``distinct()`` would count two
    # dogs sharing the same booking dates as one occupied place.
    for existing_start, existing_end in stays.values_list("start_date", "end_date"):
        overlap_start = max(existing_start, start_date)
        overlap_end = min(existing_end, end_date)
        changes_by_date[overlap_start] = changes_by_date.get(overlap_start, 0) + 1
        changes_by_date[overlap_end] = changes_by_date.get(overlap_end, 0) - 1

    occupied = 0
    for service_date in sorted(changes_by_date):
        occupied += changes_by_date[service_date]
        if occupied + requested_dogs > capacity:
            raise BookingValidationError(
                _("This accommodation is full for one or more selected nights.")
            )


@transaction.atomic
def reschedule_booking(booking, *, start_date, end_date, actor):
    """Move a booking without bypassing capacity or losing service preferences.

    The stay duration is validated by the form, but this service still treats
    the operation as untrusted.  Every affected offer is locked before its
    capacity is checked so two concurrent reschedules cannot oversell the same
    nights.  Customer preferences move by the same number of days; a confirmed
    staff appointment is cleared because silently moving it could create a
    trainer or operational conflict.
    """

    locked_booking = Booking.objects.select_for_update().get(pk=booking.pk)
    stays = list(locked_booking.stays.select_related("catalog_offer").order_by("pk"))
    if not stays:
        raise BookingValidationError(_("This booking has no accommodation to move."))

    original_start = min(stay.start_date for stay in stays)
    original_end = max(stay.end_date for stay in stays)
    if end_date <= start_date:
        raise BookingValidationError(_("Check-out must be after check-in."))
    if start_date < timezone.localdate():
        raise BookingValidationError(_("The check-in date cannot be in the past."))
    if (end_date - start_date) != (original_end - original_start):
        raise BookingValidationError(_("The original length of stay must be preserved."))

    stays_by_offer = {}
    for stay in stays:
        stays_by_offer.setdefault(stay.catalog_offer_id, []).append(stay)

    locked_offers = {
        offer.pk: offer
        for offer in CatalogOffer.objects.select_for_update()
        .select_related("catalog_type", "level")
        .filter(pk__in=stays_by_offer)
        .order_by("pk")
    }
    for offer_id, offer_stays in stays_by_offer.items():
        ensure_stay_capacity_available(
            locked_offers[offer_id],
            start_date=start_date,
            end_date=end_date,
            requested_dogs=len(offer_stays),
            exclude_booking_id=locked_booking.pk,
        )

    day_delta = start_date - original_start
    for stay in stays:
        stay.start_date = start_date
        stay.end_date = end_date
        stay.full_clean()
        stay.save(update_fields=("start_date", "end_date"))
        for service in stay.services.all():
            update_fields = []
            if service.preferred_date:
                service.preferred_date += day_delta
                update_fields.append("preferred_date")
            if service.scheduled_time:
                service.scheduled_time = None
                update_fields.append("scheduled_time")
            if update_fields:
                service.full_clean()
                service.save(update_fields=tuple(update_fields))

    from activities.services.fulfilments import reset_booking_fulfilments_after_reschedule

    cleared_appointments = reset_booking_fulfilments_after_reschedule(
        locked_booking,
        day_delta=day_delta,
        actor=actor,
    )

    BookingEvent.objects.create(
        booking=locked_booking,
        event_type=BookingEventType.RESCHEDULED,
        actor=actor,
        details={
            "original_start": original_start.isoformat(),
            "original_end": original_end.isoformat(),
            "new_start": start_date.isoformat(),
            "new_end": end_date.isoformat(),
            "cleared_confirmed_service_appointments": cleared_appointments,
        },
    )
    return locked_booking


def _validated_dates(booking_data):
    try:
        start_date = date.fromisoformat(str(booking_data["start_date"]))
        end_date = date.fromisoformat(str(booking_data["end_date"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise BookingValidationError(_("The booking dates are invalid.")) from exc

    if start_date < timezone.localdate():
        raise BookingValidationError(_("The check-in date cannot be in the past."))
    if end_date <= start_date:
        raise BookingValidationError(_("Check-out must be after check-in."))
    return start_date, end_date, (end_date - start_date).days
