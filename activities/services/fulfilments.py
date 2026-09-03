from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from activities.models import (
    CreditLedgerEntry,
    EntitlementStatus,
    LedgerReason,
    OfferingMode,
    ServiceEntitlement,
    ServiceFulfilment,
    ServiceFulfilmentEvent,
    ServiceFulfilmentStatus,
    WeeklyUsageAdjustment,
)
from activities.services.calendar import closure_reason
from catalog.models import CatalogWorkflow
from hotel.models import (
    CONFIRMED_BOOKING_STATUSES,
    Booking,
    ServiceSchedulePreference,
)

ACTIVE_FULFILMENT_STATUSES = (
    ServiceFulfilmentStatus.REQUESTED,
    ServiceFulfilmentStatus.SCHEDULED,
    ServiceFulfilmentStatus.COMPLETED,
)


class ServiceFulfilmentError(ValidationError):
    """A safe validation error for a service-delivery workflow."""


def create_awaiting_payment_fulfilment(
    entitlement,
    *,
    schedule_preference=ServiceSchedulePreference.UNSCHEDULED,
    preferred_date=None,
    preferred_time=None,
    customer_notes="",
    actor=None,
):
    """Remember a one-off service request without making unpaid work actionable."""

    entitlement = ServiceEntitlement.objects.select_related(
        "offering__catalog_offer__catalog_type",
        "pet",
    ).get(pk=entitlement.pk)
    if (
        entitlement.offering_mode_snapshot != OfferingMode.ONE_OFF
        or entitlement.offering.catalog_offer.catalog_type.workflow != CatalogWorkflow.FLEXIBLE
    ):
        return None
    preference = _validated_preference(
        schedule_preference=schedule_preference,
        preferred_date=preferred_date,
        preferred_time=preferred_time,
    )
    fulfilment, created = ServiceFulfilment.objects.get_or_create(
        entitlement=entitlement,
        source_unit_number=1,
        defaults={
            "pet": entitlement.pet,
            "status": ServiceFulfilmentStatus.AWAITING_PAYMENT,
            "preferred_date": preference["preferred_date"],
            "preferred_time": preference["preferred_time"],
            "customer_notes": str(customer_notes).strip(),
            "created_by": actor,
            "consumes_entitlement_use": False,
        },
    )
    if created:
        fulfilment.full_clean()
        fulfilment.save()
        _record_event(
            fulfilment,
            actor=actor,
            from_status="",
            to_status=ServiceFulfilmentStatus.AWAITING_PAYMENT,
            reason=_("Service request created while payment is pending."),
        )
    return fulfilment


@transaction.atomic
def activate_paid_entitlement_fulfilments(entitlement):
    """Expose a paid one-off flexible service to operations exactly once."""

    locked = (
        ServiceEntitlement.objects.select_for_update()
        .select_related("offering__catalog_offer__catalog_type", "pet")
        .get(pk=entitlement.pk)
    )
    if (
        locked.offering_mode_snapshot != OfferingMode.ONE_OFF
        or locked.offering.catalog_offer.catalog_type.workflow != CatalogWorkflow.FLEXIBLE
    ):
        return ()
    if (
        locked.status != EntitlementStatus.ACTIVE
        or not locked.paid_at
        or not locked.provider_capture_id
    ):
        raise ServiceFulfilmentError(
            _("A flexible service can only be activated after confirmed payment.")
        )

    fulfilment = (
        ServiceFulfilment.objects.select_for_update()
        .filter(
            entitlement=locked,
            source_unit_number=1,
        )
        .first()
    )
    if fulfilment is None:
        fulfilment = ServiceFulfilment.objects.create(
            entitlement=locked,
            pet=locked.pet,
            source_unit_number=1,
            status=ServiceFulfilmentStatus.AWAITING_PAYMENT,
            created_by=locked.created_by,
        )
    if fulfilment.status == ServiceFulfilmentStatus.AWAITING_PAYMENT:
        _consume_pack_credit(locked, fulfilment, actor=None)
        previous_status = fulfilment.status
        fulfilment.status = ServiceFulfilmentStatus.REQUESTED
        fulfilment.consumes_entitlement_use = True
        fulfilment.full_clean()
        fulfilment.save(update_fields=("status", "consumes_entitlement_use", "updated_at"))
        _record_event(
            fulfilment,
            actor=None,
            from_status=previous_status,
            to_status=fulfilment.status,
            reason=_("Payment confirmed; service released to operations."),
        )
    return (fulfilment,)


@transaction.atomic
def activate_confirmed_booking_fulfilments(booking):
    """Create one operational unit for every confirmed hotel add-on quantity."""

    locked_booking = Booking.objects.select_for_update().get(pk=booking.pk)
    if locked_booking.status not in CONFIRMED_BOOKING_STATUSES:
        raise ServiceFulfilmentError(
            _("Hotel services can only enter operations after booking confirmation.")
        )

    created_or_existing = []
    service_lines = locked_booking.stays.select_related("pet").prefetch_related(
        "services__catalog_offer__catalog_type"
    )
    for stay in service_lines:
        for service in stay.services.all():
            for unit_number in range(1, service.quantity + 1):
                use_preference = unit_number == 1
                fulfilment, created = ServiceFulfilment.objects.get_or_create(
                    booking_service=service,
                    source_unit_number=unit_number,
                    defaults={
                        "pet": service.pet,
                        "status": ServiceFulfilmentStatus.REQUESTED,
                        "preferred_date": service.preferred_date if use_preference else None,
                        "preferred_time": service.preferred_time if use_preference else None,
                        "customer_notes": service.notes,
                        "created_by": locked_booking.created_by,
                        "consumes_entitlement_use": False,
                    },
                )
                if created:
                    fulfilment.full_clean()
                    fulfilment.save()
                    _record_event(
                        fulfilment,
                        actor=None,
                        from_status="",
                        to_status=fulfilment.status,
                        reason=_("Hotel booking confirmed; service released to operations."),
                    )
                created_or_existing.append(fulfilment)
    return tuple(created_or_existing)


@transaction.atomic
def reset_booking_fulfilments_after_reschedule(booking, *, day_delta, actor):
    """Move open preferences and require staff to reconfirm exact appointments."""

    fulfilments = list(
        ServiceFulfilment.objects.select_for_update()
        .filter(booking_service__stay__booking=booking)
        .exclude(
            status__in=(
                ServiceFulfilmentStatus.COMPLETED,
                ServiceFulfilmentStatus.CANCELLED,
            )
        )
        .select_related("booking_service__stay")
    )
    cleared_appointments = 0
    for fulfilment in fulfilments:
        previous_status = fulfilment.status
        previous_start = fulfilment.scheduled_starts_at
        update_fields = []
        if fulfilment.preferred_date:
            fulfilment.preferred_date += day_delta
            update_fields.append("preferred_date")
        if fulfilment.scheduled_starts_at:
            fulfilment.status = ServiceFulfilmentStatus.REQUESTED
            fulfilment.scheduled_starts_at = None
            fulfilment.scheduled_ends_at = None
            update_fields.extend(("status", "scheduled_starts_at", "scheduled_ends_at"))
            cleared_appointments += 1
        if update_fields:
            fulfilment.full_clean()
            fulfilment.save(update_fields=(*dict.fromkeys(update_fields), "updated_at"))
        if previous_start:
            _record_event(
                fulfilment,
                actor=actor,
                from_status=previous_status,
                to_status=fulfilment.status,
                reason=_("Hotel dates changed; the service appointment needs reconfirmation."),
                details={"previous_starts_at": previous_start.isoformat()},
            )
    return cleared_appointments


@transaction.atomic
def request_entitlement_fulfilment(
    entitlement,
    *,
    schedule_preference=ServiceSchedulePreference.UNSCHEDULED,
    preferred_date=None,
    preferred_time=None,
    customer_notes="",
    actor=None,
):
    """Use one pack credit or one weekly allowance for a flexible service."""

    locked = (
        ServiceEntitlement.objects.select_for_update()
        .select_related("offering__catalog_offer__catalog_type", "pet")
        .get(pk=entitlement.pk)
    )
    if locked.offering.catalog_offer.catalog_type.workflow != CatalogWorkflow.FLEXIBLE:
        raise ServiceFulfilmentError(_("This service is booked through the activity calendar."))
    if not locked.has_access:
        raise ServiceFulfilmentError(_("This service is not active or has no remaining use."))

    preference = _validated_preference(
        schedule_preference=schedule_preference,
        preferred_date=preferred_date,
        preferred_time=preferred_time,
    )
    service_date = preference["preferred_date"] or timezone.localdate()
    usage_week_start = None
    if locked.offering_mode_snapshot == OfferingMode.SUBSCRIPTION:
        usage_week_start = service_date - timedelta(days=service_date.weekday())
        _validate_subscription_allowance(
            locked,
            week_start=usage_week_start,
            service_date=service_date,
        )

    fulfilment = ServiceFulfilment(
        entitlement=locked,
        pet=locked.pet,
        status=ServiceFulfilmentStatus.REQUESTED,
        preferred_date=preference["preferred_date"],
        preferred_time=preference["preferred_time"],
        usage_week_start=usage_week_start,
        consumes_entitlement_use=True,
        customer_notes=str(customer_notes).strip(),
        created_by=actor,
    )
    fulfilment.full_clean()
    fulfilment.save()
    if locked.offering_mode_snapshot in {OfferingMode.ONE_OFF, OfferingMode.PACK}:
        _consume_pack_credit(locked, fulfilment, actor=actor)
    _record_event(
        fulfilment,
        actor=actor,
        from_status="",
        to_status=fulfilment.status,
        reason=_("Customer requested service delivery."),
    )
    return fulfilment


@transaction.atomic
def schedule_fulfilment(
    fulfilment,
    *,
    starts_at,
    ends_at=None,
    actor,
    reason,
    internal_notes="",
    override_closure=False,
):
    """Set or move the operational appointment with an append-only audit event."""

    locked = _locked_fulfilment(fulfilment)
    if locked.status in {
        ServiceFulfilmentStatus.AWAITING_PAYMENT,
        ServiceFulfilmentStatus.COMPLETED,
        ServiceFulfilmentStatus.CANCELLED,
    }:
        raise ServiceFulfilmentError(_("This service cannot be scheduled in its current state."))
    reason = _required_reason(reason)
    ends_at = ends_at or (starts_at + timedelta(hours=1))
    if ends_at <= starts_at:
        raise ServiceFulfilmentError(_("The end time must be after the start time."))
    closure = closure_reason(locked.catalog_offer.catalog_type, starts_at, ends_at)
    if closure and not override_closure:
        raise ServiceFulfilmentError(_("This period is closed: %(reason)s") % {"reason": closure})

    if locked.booking_service_id:
        stay = locked.booking_service.stay
        local_start_date = timezone.localtime(starts_at).date()
        if not stay.start_date <= local_start_date < stay.end_date:
            raise ServiceFulfilmentError(_("Choose an appointment during the hotel stay."))

    previous_status = locked.status
    previous_start = locked.scheduled_starts_at
    previous_end = locked.scheduled_ends_at
    locked.status = ServiceFulfilmentStatus.SCHEDULED
    locked.scheduled_starts_at = starts_at
    locked.scheduled_ends_at = ends_at
    locked.internal_notes = str(internal_notes).strip()
    locked.full_clean()
    locked.save(
        update_fields=(
            "status",
            "scheduled_starts_at",
            "scheduled_ends_at",
            "internal_notes",
            "updated_at",
        )
    )
    _record_event(
        locked,
        actor=actor,
        from_status=previous_status,
        to_status=locked.status,
        reason=reason,
        details={
            "previous_starts_at": previous_start.isoformat() if previous_start else None,
            "previous_ends_at": previous_end.isoformat() if previous_end else None,
            "new_starts_at": starts_at.isoformat(),
            "new_ends_at": ends_at.isoformat(),
            "closure_overridden": bool(closure and override_closure),
        },
    )
    return locked


@transaction.atomic
def complete_fulfilment(fulfilment, *, actor, reason, internal_notes=""):
    locked = _locked_fulfilment(fulfilment)
    if locked.status not in {
        ServiceFulfilmentStatus.REQUESTED,
        ServiceFulfilmentStatus.SCHEDULED,
    }:
        raise ServiceFulfilmentError(_("This service cannot be completed in its current state."))
    reason = _required_reason(reason)
    previous_status = locked.status
    locked.status = ServiceFulfilmentStatus.COMPLETED
    locked.completed_at = timezone.now()
    locked.completed_by = actor
    if str(internal_notes).strip():
        locked.internal_notes = str(internal_notes).strip()
    locked.full_clean()
    locked.save(
        update_fields=(
            "status",
            "completed_at",
            "completed_by",
            "internal_notes",
            "updated_at",
        )
    )
    _record_event(
        locked,
        actor=actor,
        from_status=previous_status,
        to_status=locked.status,
        reason=reason,
    )
    return locked


@transaction.atomic
def cancel_fulfilment(
    fulfilment,
    *,
    actor,
    reason,
    override_policy=False,
):
    """Cancel delivery and return usage only when policy permits it."""

    locked = _locked_fulfilment(fulfilment)
    if locked.status in {
        ServiceFulfilmentStatus.COMPLETED,
        ServiceFulfilmentStatus.CANCELLED,
    }:
        raise ServiceFulfilmentError(_("This service cannot be cancelled in its current state."))
    reason = _required_reason(reason)

    policy_honoured = True
    if locked.scheduled_starts_at:
        if locked.scheduled_starts_at <= timezone.now():
            raise ServiceFulfilmentError(_("A service that has started cannot be cancelled."))
        deadline = locked.scheduled_starts_at - timedelta(
            hours=locked.catalog_offer.effective_cancellation_notice_hours
        )
        policy_honoured = timezone.now() <= deadline or override_policy

    previous_status = locked.status
    locked.status = ServiceFulfilmentStatus.CANCELLED
    locked.cancelled_at = timezone.now()
    locked.cancellation_reason = reason
    locked.cancellation_policy_overridden = override_policy
    if policy_honoured:
        locked.consumes_entitlement_use = False
    locked.full_clean()
    locked.save(
        update_fields=(
            "status",
            "cancelled_at",
            "cancellation_reason",
            "cancellation_policy_overridden",
            "consumes_entitlement_use",
            "updated_at",
        )
    )
    credit_returned = False
    if (
        locked.entitlement_id
        and locked.entitlement.offering_mode_snapshot in {OfferingMode.ONE_OFF, OfferingMode.PACK}
        and policy_honoured
        and previous_status != ServiceFulfilmentStatus.AWAITING_PAYMENT
    ):
        _ledger_entry, credit_returned = CreditLedgerEntry.objects.get_or_create(
            idempotency_key=f"fulfilment-cancellation-{locked.pk}",
            defaults={
                "entitlement": locked.entitlement,
                "delta": 1,
                "reason": LedgerReason.CANCELLATION,
                "note": locked.entitlement.item_name_snapshot,
                "created_by": actor,
            },
        )
    _record_event(
        locked,
        actor=actor,
        from_status=previous_status,
        to_status=locked.status,
        reason=reason,
        details={
            "policy_overridden": override_policy,
            "usage_returned": policy_honoured,
        },
    )
    return locked, credit_returned


def _locked_fulfilment(fulfilment):
    return (
        ServiceFulfilment.objects.select_for_update()
        .select_related(
            "entitlement__offering__catalog_offer__catalog_type",
            "booking_service__catalog_offer__catalog_type",
            "booking_service__stay",
        )
        .get(pk=fulfilment.pk)
    )


def _validated_preference(*, schedule_preference, preferred_date, preferred_time):
    if schedule_preference not in ServiceSchedulePreference.values:
        raise ServiceFulfilmentError(_("The scheduling preference is invalid."))
    if preferred_date and preferred_date < timezone.localdate():
        raise ServiceFulfilmentError(_("The preferred date cannot be in the past."))
    if schedule_preference == ServiceSchedulePreference.UNSCHEDULED:
        if preferred_date or preferred_time:
            raise ServiceFulfilmentError(_("Remove the date and time for an unscheduled request."))
    elif schedule_preference == ServiceSchedulePreference.DATE:
        if not preferred_date or preferred_time:
            raise ServiceFulfilmentError(_("Choose one preferred date without a time."))
    elif schedule_preference == ServiceSchedulePreference.DATE_TIME:
        if not preferred_date or not preferred_time:
            raise ServiceFulfilmentError(_("Choose both a preferred date and time."))
    return {
        "preferred_date": preferred_date,
        "preferred_time": preferred_time,
    }


def _consume_pack_credit(entitlement, fulfilment, *, actor):
    balance = entitlement.credit_entries.aggregate(total=Sum("delta"))["total"] or 0
    if balance < 1:
        raise ServiceFulfilmentError(_("This pack has no remaining use."))
    CreditLedgerEntry.objects.get_or_create(
        idempotency_key=f"service-fulfilment-{fulfilment.pk}",
        defaults={
            "entitlement": entitlement,
            "delta": -1,
            "reason": LedgerReason.RESERVATION,
            "note": entitlement.item_name_snapshot,
            "created_by": actor,
        },
    )


def _validate_subscription_allowance(entitlement, *, week_start, service_date):
    if (
        entitlement.current_period_start
        and service_date < timezone.localtime(entitlement.current_period_start).date()
    ):
        raise ServiceFulfilmentError(_("The chosen week is outside the paid subscription period."))
    if (
        entitlement.current_period_end
        and service_date >= timezone.localtime(entitlement.current_period_end).date()
    ):
        raise ServiceFulfilmentError(_("The chosen week is outside the paid subscription period."))
    used = ServiceFulfilment.objects.filter(
        entitlement=entitlement,
        usage_week_start=week_start,
        consumes_entitlement_use=True,
    ).count()
    extra_uses = (
        WeeklyUsageAdjustment.objects.filter(
            entitlement=entitlement,
            week_start=week_start,
        ).aggregate(total=Sum("extra_uses"))["total"]
        or 0
    )
    allowance = (entitlement.sessions_per_week_snapshot or 0) + extra_uses
    if used >= allowance:
        raise ServiceFulfilmentError(_("The weekly limit for this subscription is reached."))


def _required_reason(reason):
    value = str(reason or "").strip()
    if not value:
        raise ServiceFulfilmentError(_("A reason is required for the audit history."))
    return value


def _record_event(
    fulfilment,
    *,
    actor,
    from_status,
    to_status,
    reason,
    details=None,
):
    ServiceFulfilmentEvent.objects.create(
        fulfilment=fulfilment,
        actor=actor,
        from_status=from_status,
        to_status=to_status,
        reason=str(reason),
        details=details or {},
    )
