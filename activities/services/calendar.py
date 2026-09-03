from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from activities.models import ActivitySession, OperationalClosure, SessionSeries
from catalog.models import CatalogWorkflow


def _gregorian_easter(year):
    """Return Gregorian Easter Sunday using the Meeus/Jones/Butcher algorithm."""

    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def portuguese_national_holidays(year):
    """Return Portugal's mandatory national public holidays for ``year``.

    Carnival and municipal holidays are intentionally excluded because their
    observance is optional and must be recorded as explicit calendar closures.
    """

    easter = _gregorian_easter(year)
    return {
        date(year, 1, 1): _("New Year's Day"),
        easter - timedelta(days=2): _("Good Friday"),
        easter: _("Easter Sunday"),
        date(year, 4, 25): _("Freedom Day"),
        date(year, 5, 1): _("Labour Day"),
        easter + timedelta(days=60): _("Corpus Christi"),
        date(year, 6, 10): _("Portugal Day"),
        date(year, 8, 15): _("Assumption Day"),
        date(year, 10, 5): _("Republic Day"),
        date(year, 11, 1): _("All Saints' Day"),
        date(year, 12, 1): _("Restoration of Independence"),
        date(year, 12, 8): _("Immaculate Conception"),
        date(year, 12, 25): _("Christmas Day"),
    }


def _local_dates_between(starts_at, ends_at):
    current = timezone.localtime(starts_at).date()
    final = timezone.localtime(ends_at - timedelta(microseconds=1)).date()
    while current <= final:
        yield current
        current += timedelta(days=1)


def national_holiday_closure(catalog_type, starts_at, ends_at):
    if catalog_type.workflow == CatalogWorkflow.DATE_RANGE:
        return None
    if not catalog_type.closes_on_national_holidays:
        return None
    for local_date in _local_dates_between(starts_at, ends_at):
        holiday = portuguese_national_holidays(local_date.year).get(local_date)
        if holiday:
            return holiday
    return None


def manual_closure(catalog_type, starts_at, ends_at):
    if catalog_type.workflow == CatalogWorkflow.DATE_RANGE:
        return None
    return (
        OperationalClosure.objects.filter(
            is_active=True,
            starts_at__lt=ends_at,
            ends_at__gt=starts_at,
        )
        .filter(Q(all_operational_services=True) | Q(catalog_types=catalog_type))
        .distinct()
        .order_by("starts_at", "pk")
        .first()
    )


def closure_reason(catalog_type, starts_at, ends_at):
    holiday = national_holiday_closure(catalog_type, starts_at, ends_at)
    if holiday:
        return str(holiday)
    closure = manual_closure(catalog_type, starts_at, ends_at)
    if closure:
        return closure.customer_message or closure.name
    return ""


def is_closed(catalog_type, starts_at, ends_at):
    return bool(closure_reason(catalog_type, starts_at, ends_at))


@dataclass(frozen=True)
class MaterializationResult:
    created: int = 0
    existing: int = 0
    skipped_closed: int = 0
    trainer_conflicts: int = 0


def _aware_datetime(local_date, local_time):
    naive = datetime.combine(local_date, local_time)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def trainers_have_conflict(
    trainers,
    starts_at,
    ends_at,
    *,
    exclude_session_id=None,
):
    """Return whether any selected trainer already has an overlapping session."""

    trainer_ids = [trainer.pk for trainer in trainers]
    if not trainer_ids:
        return False
    sessions = ActivitySession.objects.filter(
        is_cancelled=False,
        trainers__in=trainer_ids,
        starts_at__lt=ends_at,
        ends_at__gt=starts_at,
    )
    if exclude_session_id:
        sessions = sessions.exclude(pk=exclude_session_id)
    return sessions.exists()


@transaction.atomic
def materialize_session_series(series):
    """Idempotently create concrete sessions for one weekly schedule rule."""

    series = (
        SessionSeries.objects.select_for_update()
        .select_related("catalog_type")
        .prefetch_related("eligible_levels", "trainers")
        .get(pk=series.pk)
    )
    series.full_clean()
    eligible_levels = tuple(series.eligible_levels.all())
    trainers = tuple(series.trainers.filter(is_active=True))
    allowed_weekdays = set(series.weekdays)
    counts = {"created": 0, "existing": 0, "skipped_closed": 0, "trainer_conflicts": 0}
    current_date = series.starts_on

    while current_date <= series.ends_on:
        if current_date.isoweekday() not in allowed_weekdays:
            current_date += timedelta(days=1)
            continue
        starts_at = _aware_datetime(current_date, series.starts_at_time)
        ends_at = _aware_datetime(current_date, series.ends_at_time)
        if series.skip_closed_days and is_closed(series.catalog_type, starts_at, ends_at):
            counts["skipped_closed"] += 1
            current_date += timedelta(days=1)
            continue
        existing = ActivitySession.objects.filter(
            source_series=series,
            starts_at=starts_at,
        ).first()
        if existing:
            counts["existing"] += 1
            current_date += timedelta(days=1)
            continue
        if trainers_have_conflict(trainers, starts_at, ends_at):
            counts["trainer_conflicts"] += 1
            current_date += timedelta(days=1)
            continue
        session = ActivitySession.objects.create(
            title=series.title,
            catalog_type=series.catalog_type,
            source_series=series,
            starts_at=starts_at,
            ends_at=ends_at,
            capacity=series.effective_capacity,
            notes=series.notes,
            created_by=series.created_by,
        )
        session.eligible_levels.set(eligible_levels)
        session.trainers.set(trainers)
        counts["created"] += 1
        current_date += timedelta(days=1)
    return MaterializationResult(**counts)
