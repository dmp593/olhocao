import logging
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from activities.models import (
    CONSUMED_WEEKLY_USAGE_STATUSES,
    RESERVED_PLACE_STATUSES,
    ActivityReservation,
    ActivityReservationEvent,
    ActivitySession,
    ActivitySessionEvent,
    CreditLedgerEntry,
    EntitlementStatus,
    LedgerReason,
    OfferingMode,
    ReservationStatus,
    ServiceEntitlement,
    WeeklyUsageAdjustment,
)
from activities.services.calendar import closure_reason


class ActivityReservationError(ValidationError):
    pass


logger = logging.getLogger(__name__)


def reserve_session(*, entitlement, session, actor, notes=""):
    with transaction.atomic():
        locked_session = ActivitySession.objects.select_for_update().get(pk=session.pk)
        locked_entitlement = (
            ServiceEntitlement.objects.select_for_update()
            .select_related(
                "offering__catalog_offer__catalog_type",
                "offering__catalog_offer__level",
                "pet",
            )
            .get(pk=entitlement.pk)
        )
        _validate_reservation(locked_entitlement, locked_session)

        existing = ActivityReservation.objects.filter(
            session=locked_session,
            pet=locked_entitlement.pet,
            status__in=RESERVED_PLACE_STATUSES,
        ).first()
        if existing:
            return existing

        reservation = ActivityReservation(
            session=locked_session,
            entitlement=locked_entitlement,
            pet=locked_entitlement.pet,
            notes=notes,
            created_by=actor,
        )
        reservation.full_clean()
        reservation.save()
        ActivityReservationEvent.objects.create(
            reservation=reservation,
            actor=actor,
            from_status="",
            to_status=ReservationStatus.BOOKED,
        )
        if locked_entitlement.offering_mode_snapshot in {
            OfferingMode.ONE_OFF,
            OfferingMode.PACK,
        }:
            CreditLedgerEntry.objects.create(
                entitlement=locked_entitlement,
                delta=-1,
                reason=LedgerReason.RESERVATION,
                idempotency_key=f"activity-reservation-{reservation.pk}",
                note=locked_session.title,
                created_by=actor,
            )
        return reservation


def cancel_reservation(
    reservation,
    *,
    actor,
    override_policy=False,
    reason="",
):
    with transaction.atomic():
        locked = (
            ActivityReservation.objects.select_for_update()
            .select_related("session", "entitlement__offering")
            .get(pk=reservation.pk)
        )
        if locked.status != ReservationStatus.BOOKED:
            return locked, False
        if locked.session.starts_at <= timezone.now():
            raise ActivityReservationError(_("A session that has started cannot be cancelled."))

        deadline = locked.session.starts_at - timedelta(
            hours=locked.entitlement.cancellation_notice_hours_snapshot
        )
        before_deadline = timezone.now() <= deadline
        if override_policy and not str(reason).strip():
            raise ActivityReservationError(_("A reason is required to override the policy."))
        policy_honoured = before_deadline or override_policy
        credit_returned = (
            locked.entitlement.offering_mode_snapshot in {OfferingMode.ONE_OFF, OfferingMode.PACK}
            and policy_honoured
        )
        locked.status = (
            ReservationStatus.CANCELLED if policy_honoured else ReservationStatus.LATE_CANCELLED
        )
        locked.cancelled_at = timezone.now()
        locked.cancellation_reason = str(reason).strip()
        locked.cancellation_policy_overridden = override_policy
        locked.save(
            update_fields=(
                "status",
                "cancelled_at",
                "cancellation_reason",
                "cancellation_policy_overridden",
            )
        )
        ActivityReservationEvent.objects.create(
            reservation=locked,
            actor=actor,
            from_status=ReservationStatus.BOOKED,
            to_status=locked.status,
            note=locked.cancellation_reason,
        )
        if credit_returned:
            CreditLedgerEntry.objects.get_or_create(
                idempotency_key=f"activity-cancellation-{locked.pk}",
                defaults={
                    "entitlement": locked.entitlement,
                    "delta": 1,
                    "reason": LedgerReason.CANCELLATION,
                    "note": locked.session.title,
                    "created_by": actor,
                },
            )
        return locked, credit_returned


def cancel_activity_session(session, *, actor, reason):
    """Cancel one future session and release every affected reservation."""

    reason = str(reason or "").strip()
    if not reason:
        raise ActivityReservationError(_("A reason is required to cancel the session."))

    with transaction.atomic():
        locked_session = ActivitySession.objects.select_for_update().get(pk=session.pk)
        if locked_session.is_cancelled:
            return locked_session, 0
        if locked_session.starts_at <= timezone.now():
            raise ActivityReservationError(_("A session that has started cannot be cancelled."))

        reservations = list(
            ActivityReservation.objects.select_related(
                "entitlement__customer__user",
                "entitlement__offering",
            ).filter(
                session=locked_session,
                status=ReservationStatus.BOOKED,
            )
        )
        credits_returned = 0
        for reservation in reservations:
            _cancelled_reservation, credit_returned = cancel_reservation(
                reservation,
                actor=actor,
                override_policy=True,
                reason=reason,
            )
            credits_returned += int(credit_returned)

        locked_session.is_cancelled = True
        locked_session.save(update_fields=("is_cancelled", "updated_at"))
        ActivitySessionEvent.objects.create(
            session=locked_session,
            action="cancelled",
            actor=actor,
            details={
                "reason": reason,
                "reservations_cancelled": len(reservations),
                "pack_credits_returned": credits_returned,
            },
        )
        notification_rows = [
            (
                reservation.entitlement.customer.user.email,
                reservation.entitlement.customer.user.get_full_name()
                or reservation.entitlement.customer.user.username,
                reservation.pet.name,
            )
            for reservation in reservations
            if reservation.entitlement.customer.user.email
        ]
        transaction.on_commit(
            lambda: _send_session_cancellation_notifications(
                locked_session,
                notification_rows,
                reason=reason,
            )
        )
        return locked_session, len(reservations)


def _send_session_cancellation_notifications(session, recipients, *, reason):
    for email, customer_name, pet_name in recipients:
        try:
            send_mail(
                _("Session cancelled: %(title)s") % {"title": session.title},
                _(
                    "Hello %(name)s,\n\n"
                    "The session %(title)s on %(date)s for %(pet)s was cancelled.\n"
                    "Reason: %(reason)s\n\n"
                    "Any applicable pack credit was returned automatically. "
                    "Please sign in to choose another available date."
                )
                % {
                    "name": customer_name,
                    "title": session.title,
                    "date": timezone.localtime(session.starts_at).strftime("%d/%m/%Y %H:%M"),
                    "pet": pet_name,
                    "reason": reason,
                },
                settings.DEFAULT_FROM_EMAIL,
                [email],
            )
        except Exception:
            logger.exception(
                "Could not email an activity session cancellation",
                extra={"activity_session_id": session.pk},
            )


def notify_activity_session_rescheduled(
    session,
    *,
    previous_starts_at,
    previous_ends_at,
):
    """Notify booked customers after a management schedule change is committed."""

    if not _session_period_changed(
        session.starts_at,
        session.ends_at,
        previous_starts_at,
        previous_ends_at,
    ):
        return

    recipients = [
        (
            reservation.entitlement.customer.user.email,
            reservation.entitlement.customer.user.get_full_name()
            or reservation.entitlement.customer.user.username,
            reservation.pet.name,
        )
        for reservation in ActivityReservation.objects.select_related(
            "entitlement__customer__user",
            "pet",
        ).filter(
            session=session,
            status=ReservationStatus.BOOKED,
        )
        if reservation.entitlement.customer.user.email
    ]
    if not recipients:
        return

    transaction.on_commit(
        lambda: _send_session_rescheduling_notifications(
            session,
            recipients,
            previous_starts_at=previous_starts_at,
            previous_ends_at=previous_ends_at,
        )
    )


def _session_period_changed(starts_at, ends_at, previous_starts_at, previous_ends_at):
    """Compare the minute precision exposed by the management form."""

    current = (
        starts_at.replace(second=0, microsecond=0),
        ends_at.replace(second=0, microsecond=0),
    )
    previous = (
        previous_starts_at.replace(second=0, microsecond=0),
        previous_ends_at.replace(second=0, microsecond=0),
    )
    return current != previous


def _send_session_rescheduling_notifications(
    session,
    recipients,
    *,
    previous_starts_at,
    previous_ends_at,
):
    previous_period = _format_session_period(previous_starts_at, previous_ends_at)
    new_period = _format_session_period(session.starts_at, session.ends_at)
    for email, customer_name, pet_name in recipients:
        try:
            send_mail(
                _("Session rescheduled: %(title)s") % {"title": session.title},
                _(
                    "Hello %(name)s,\n\n"
                    "The session %(title)s for %(pet)s was rescheduled.\n"
                    "Previous schedule: %(previous_period)s\n"
                    "New schedule: %(new_period)s\n\n"
                    "Your reservation remains confirmed. Please contact us if the new "
                    "schedule is not suitable."
                )
                % {
                    "name": customer_name,
                    "title": session.title,
                    "pet": pet_name,
                    "previous_period": previous_period,
                    "new_period": new_period,
                },
                settings.DEFAULT_FROM_EMAIL,
                [email],
            )
        except Exception:
            logger.exception(
                "Could not email an activity session rescheduling",
                extra={"activity_session_id": session.pk},
            )


def _format_session_period(starts_at, ends_at):
    local_start = timezone.localtime(starts_at)
    local_end = timezone.localtime(ends_at)
    if local_start.date() == local_end.date():
        return _("%(date)s, %(start)s–%(end)s") % {
            "date": local_start.strftime("%d/%m/%Y"),
            "start": local_start.strftime("%H:%M"),
            "end": local_end.strftime("%H:%M"),
        }
    return _("%(start)s to %(end)s") % {
        "start": local_start.strftime("%d/%m/%Y %H:%M"),
        "end": local_end.strftime("%d/%m/%Y %H:%M"),
    }


@transaction.atomic
def set_reservation_attendance(reservation, *, status, actor, note=""):
    """Record or correct attendance without bypassing time and capacity rules."""

    allowed_statuses = {
        ReservationStatus.BOOKED,
        ReservationStatus.ATTENDED,
        ReservationStatus.NO_SHOW,
    }
    if status not in allowed_statuses:
        raise ActivityReservationError(_("That reservation status change is not allowed."))

    locked = (
        ActivityReservation.objects.select_for_update()
        .select_related("session")
        .get(pk=reservation.pk)
    )
    if locked.status in {
        ReservationStatus.CANCELLED,
        ReservationStatus.LATE_CANCELLED,
    }:
        raise ActivityReservationError(_("A cancelled reservation cannot be reopened."))
    if locked.status == status:
        return locked

    now = timezone.now()
    if status in {ReservationStatus.ATTENDED, ReservationStatus.NO_SHOW}:
        if locked.session.starts_at > now:
            raise ActivityReservationError(
                _("Attendance can only be recorded after the session starts.")
            )
    elif status == ReservationStatus.BOOKED:
        if locked.session.ends_at <= now:
            raise ActivityReservationError(
                _("A finished session cannot be restored to an upcoming reservation.")
            )
        occupied = (
            locked.session.reservations.filter(status__in=RESERVED_PLACE_STATUSES)
            .exclude(pk=locked.pk)
            .count()
        )
        if occupied >= locked.session.capacity:
            raise ActivityReservationError(_("This session no longer has a free place."))

    previous_status = locked.status
    locked.status = status
    locked.save(update_fields=("status",))
    ActivityReservationEvent.objects.create(
        reservation=locked,
        actor=actor,
        from_status=previous_status,
        to_status=status,
        note=str(note).strip(),
    )
    return locked


def _validate_reservation(entitlement, session):
    now = timezone.now()
    if session.is_cancelled or session.starts_at <= now:
        raise ActivityReservationError(_("This session is no longer available."))
    if not entitlement.has_access:
        raise ActivityReservationError(_("This service is not active or has no remaining credit."))
    if session.catalog_type_id != entitlement.catalog_type_reference:
        raise ActivityReservationError(_("This service does not include that activity."))
    if entitlement.current_period_start and session.starts_at < entitlement.current_period_start:
        raise ActivityReservationError(
            _("This session is before the purchased access period starts.")
        )
    if entitlement.current_period_end and session.starts_at >= entitlement.current_period_end:
        raise ActivityReservationError(_("This session is outside the purchased access period."))
    eligible_level_ids = set(session.eligible_levels.values_list("pk", flat=True))
    if eligible_level_ids and entitlement.catalog_level_reference not in eligible_level_ids:
        raise ActivityReservationError(_("This service level cannot book that session."))
    closed_message = closure_reason(session.catalog_type, session.starts_at, session.ends_at)
    if closed_message:
        raise ActivityReservationError(
            _("Reservations are closed for this period: %(reason)s") % {"reason": closed_message}
        )
    if session.reservations.filter(status__in=RESERVED_PLACE_STATUSES).count() >= session.capacity:
        raise ActivityReservationError(_("This session is full."))
    if (
        entitlement.offering_mode_snapshot in {OfferingMode.ONE_OFF, OfferingMode.PACK}
        and entitlement.credit_balance < 1
    ):
        raise ActivityReservationError(_("This pack has no remaining sessions."))
    if entitlement.offering_mode_snapshot == OfferingMode.SUBSCRIPTION:
        local_start = timezone.localtime(session.starts_at)
        week_start = local_start - timedelta(days=local_start.weekday())
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        week_end = week_start + timedelta(days=7)
        weekly_count = ActivityReservation.objects.filter(
            entitlement=entitlement,
            status__in=CONSUMED_WEEKLY_USAGE_STATUSES,
            session__starts_at__gte=week_start,
            session__starts_at__lt=week_end,
        ).count()
        extra_uses = (
            WeeklyUsageAdjustment.objects.filter(
                entitlement=entitlement,
                week_start=week_start.date(),
            ).aggregate(total=Sum("extra_uses"))["total"]
            or 0
        )
        allowance = entitlement.sessions_per_week_snapshot + extra_uses
        if weekly_count >= allowance:
            raise ActivityReservationError(_("The weekly limit for this subscription is reached."))


def expire_finished_entitlements():
    """Idempotent maintenance hook used by the daily management command."""
    now = timezone.now()
    cancelled_count = ServiceEntitlement.objects.filter(
        status=EntitlementStatus.CANCEL_AT_PERIOD_END,
        current_period_end__lte=now,
    ).update(status=EntitlementStatus.CANCELLED, updated_at=now)
    past_due_count = ServiceEntitlement.objects.filter(
        offering_mode_snapshot=OfferingMode.SUBSCRIPTION,
        status=EntitlementStatus.ACTIVE,
        current_period_end__lte=now,
    ).update(
        status=EntitlementStatus.PAST_DUE,
        last_payment_error=_("No successful renewal was received before the paid period ended."),
        updated_at=now,
    )
    expired_pack_count = ServiceEntitlement.objects.filter(
        offering_mode_snapshot__in=(OfferingMode.ONE_OFF, OfferingMode.PACK),
        status=EntitlementStatus.ACTIVE,
        current_period_end__lte=now,
    ).update(status=EntitlementStatus.EXPIRED, updated_at=now)
    return cancelled_count + past_due_count + expired_pack_count
