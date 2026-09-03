from datetime import date, datetime, time, timedelta

from django.test import TestCase
from django.utils import timezone

from activities.models import (
    ActivitySession,
    OperationalClosure,
    SessionSeries,
    Trainer,
)
from activities.services.calendar import (
    closure_reason,
    materialize_session_series,
    portuguese_national_holidays,
)
from activities.tests.factories import create_catalog_type
from catalog.models import CatalogWorkflow


def aware(year, month, day, hour=9):
    return timezone.make_aware(datetime(year, month, day, hour, 0))


class PortugalCalendarTests(TestCase):
    def test_national_holiday_is_derived_for_each_year(self):
        holidays = portuguese_national_holidays(2027)

        self.assertIn(date(2027, 3, 26), holidays)  # Good Friday
        self.assertIn(date(2027, 6, 10), holidays)
        self.assertNotIn(date(2027, 3, 27), holidays)

    def test_scheduled_type_can_close_on_national_holidays(self):
        catalog_type = create_catalog_type(
            workflow=CatalogWorkflow.FULL_DAY_SLOT,
            slug="holiday-sensitive",
        )
        catalog_type.closes_on_national_holidays = True
        catalog_type.save(update_fields=("closes_on_national_holidays", "updated_at"))

        reason = closure_reason(
            catalog_type,
            aware(2027, 6, 10),
            aware(2027, 6, 10, 18),
        )

        self.assertIn("Portugal", reason)

    def test_date_range_accommodation_ignores_every_closure(self):
        hotel_type = create_catalog_type(
            workflow=CatalogWorkflow.DATE_RANGE,
            slug="always-open-accommodation",
            capacity=20,
        )
        hotel_type.closes_on_national_holidays = True
        hotel_type.save(update_fields=("closes_on_national_holidays", "updated_at"))
        OperationalClosure.objects.create(
            name="Whole centre holiday",
            starts_at=aware(2027, 6, 9),
            ends_at=aware(2027, 6, 12),
            all_operational_services=True,
            reason="Team leave",
        )

        reason = closure_reason(
            hotel_type,
            aware(2027, 6, 10),
            aware(2027, 6, 11),
        )

        self.assertEqual(reason, "")

    def test_all_service_closure_blocks_non_accommodation_types(self):
        daycare_type = create_catalog_type(
            workflow=CatalogWorkflow.FULL_DAY_SLOT,
            slug="closed-daycare",
        )
        OperationalClosure.objects.create(
            name="Team holiday",
            starts_at=aware(2027, 7, 1),
            ends_at=aware(2027, 7, 8),
            all_operational_services=True,
            reason="Annual leave",
            customer_message="Closed for team holidays",
        )

        reason = closure_reason(
            daycare_type,
            aware(2027, 7, 3),
            aware(2027, 7, 3, 18),
        )

        self.assertEqual(reason, "Closed for team holidays")


class SessionSeriesMaterializationTests(TestCase):
    def setUp(self):
        self.catalog_type = create_catalog_type(
            workflow=CatalogWorkflow.TIMED_SLOT,
            slug="scheduled-training",
            capacity=8,
        )
        self.trainer = Trainer.objects.create(name="Trainer Ana")

    def create_series(self, *, starts_on, ends_on=None, weekdays=None):
        series = SessionSeries.objects.create(
            catalog_type=self.catalog_type,
            title="Training class",
            starts_on=starts_on,
            ends_on=ends_on or starts_on,
            weekdays=weekdays or [starts_on.isoweekday()],
            starts_at_time=time(10, 0),
            ends_at_time=time(11, 0),
        )
        series.trainers.add(self.trainer)
        return series

    def test_materialization_is_idempotent(self):
        monday = date(2027, 1, 4)
        series = self.create_series(
            starts_on=monday,
            ends_on=monday + timedelta(days=14),
            weekdays=[1],
        )

        first = materialize_session_series(series)
        second = materialize_session_series(series)

        self.assertEqual(first.created, 3)
        self.assertEqual(second.existing, 3)
        self.assertEqual(ActivitySession.objects.filter(source_series=series).count(), 3)

    def test_closed_date_is_skipped(self):
        session_date = date(2027, 2, 8)
        series = self.create_series(starts_on=session_date)
        closure = OperationalClosure.objects.create(
            name="Maintenance",
            starts_at=aware(2027, 2, 8, 8),
            ends_at=aware(2027, 2, 8, 18),
            reason="Site maintenance",
        )
        closure.catalog_types.add(self.catalog_type)

        result = materialize_session_series(series)

        self.assertEqual(result.skipped_closed, 1)
        self.assertEqual(result.created, 0)

    def test_trainer_cannot_be_double_booked(self):
        session_date = date(2027, 2, 15)
        existing = ActivitySession.objects.create(
            title="Existing class",
            catalog_type=self.catalog_type,
            starts_at=aware(2027, 2, 15, 9),
            ends_at=aware(2027, 2, 15, 11),
            capacity=8,
        )
        existing.trainers.add(self.trainer)
        series = self.create_series(starts_on=session_date)

        result = materialize_session_series(series)

        self.assertEqual(result.trainer_conflicts, 1)
        self.assertEqual(result.created, 0)
