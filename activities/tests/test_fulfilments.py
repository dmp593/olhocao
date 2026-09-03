from datetime import time, timedelta

from django.test import TestCase
from django.utils import timezone

from activities.models import (
    CreditLedgerEntry,
    EntitlementStatus,
    LedgerReason,
    OfferingMode,
    OperationalClosure,
    ServiceFulfilment,
    ServiceFulfilmentStatus,
    WeeklyUsageAdjustment,
)
from activities.services.fulfilments import (
    ServiceFulfilmentError,
    activate_confirmed_booking_fulfilments,
    activate_paid_entitlement_fulfilments,
    cancel_fulfilment,
    request_entitlement_fulfilment,
    schedule_fulfilment,
)
from activities.tests.factories import create_offering
from catalog.models import CatalogWorkflow
from hotel.models import BookingStatus, ServiceSchedulePreference
from hotel.tests.factories import add_booking_extra, create_account, create_booking, create_pet


class ServiceFulfilmentTests(TestCase):
    def setUp(self):
        self.customer = create_account()
        self.pet = create_pet(self.customer)
        self.actor = self.customer.user

    def create_entitlement(self, *, mode=OfferingMode.PACK, credits=2):
        offering = create_offering(mode=mode, workflow=CatalogWorkflow.FLEXIBLE)
        entitlement = offering.entitlements.create(
            customer=self.customer,
            pet=self.pet,
            status=EntitlementStatus.ACTIVE,
            item_name_snapshot=offering.catalog_item.name,
            unit_price_eur_snapshot=offering.price_eur,
            merchant_reference=f"flexible-{offering.pk}",
            paid_at=timezone.now(),
            provider_capture_id="capture-1",
            current_period_start=timezone.now() - timedelta(days=2),
            current_period_end=timezone.now() + timedelta(days=60),
        )
        if credits:
            CreditLedgerEntry.objects.create(
                entitlement=entitlement,
                delta=credits,
                reason=LedgerReason.PURCHASE,
                idempotency_key=f"purchase-{entitlement.pk}",
            )
        return entitlement

    def test_paid_one_off_becomes_one_operational_request_and_consumes_its_use(self):
        entitlement = self.create_entitlement(mode=OfferingMode.ONE_OFF, credits=1)

        first = activate_paid_entitlement_fulfilments(entitlement)
        second = activate_paid_entitlement_fulfilments(entitlement)

        self.assertEqual(first[0].status, ServiceFulfilmentStatus.REQUESTED)
        self.assertEqual(second[0].pk, first[0].pk)
        self.assertEqual(ServiceFulfilment.objects.count(), 1)
        self.assertEqual(entitlement.credit_balance, 0)

    def test_unpaid_one_off_cannot_enter_operations(self):
        entitlement = self.create_entitlement(mode=OfferingMode.ONE_OFF, credits=0)
        entitlement.status = EntitlementStatus.PENDING
        entitlement.paid_at = None
        entitlement.provider_capture_id = ""
        entitlement.save(update_fields=("status", "paid_at", "provider_capture_id"))

        with self.assertRaises(ServiceFulfilmentError):
            activate_paid_entitlement_fulfilments(entitlement)

        self.assertFalse(ServiceFulfilment.objects.exists())

    def test_pack_request_consumes_and_timely_cancellation_returns_one_credit(self):
        entitlement = self.create_entitlement(credits=2)
        preferred_date = timezone.localdate() + timedelta(days=5)
        fulfilment = request_entitlement_fulfilment(
            entitlement,
            schedule_preference=ServiceSchedulePreference.DATE,
            preferred_date=preferred_date,
            actor=self.actor,
        )
        self.assertEqual(entitlement.credit_balance, 1)

        schedule_fulfilment(
            fulfilment,
            starts_at=timezone.now() + timedelta(days=4),
            ends_at=timezone.now() + timedelta(days=4, hours=1),
            actor=self.actor,
            reason="Confirmed by phone",
        )
        _, credit_returned = cancel_fulfilment(
            fulfilment,
            actor=self.actor,
            reason="Customer plans changed",
        )

        self.assertTrue(credit_returned)
        self.assertEqual(entitlement.credit_balance, 2)

    def test_late_cancellation_does_not_return_pack_credit(self):
        entitlement = self.create_entitlement(credits=1)
        fulfilment = request_entitlement_fulfilment(entitlement, actor=self.actor)
        schedule_fulfilment(
            fulfilment,
            starts_at=timezone.now() + timedelta(hours=2),
            actor=self.actor,
            reason="Confirmed by phone",
        )

        cancelled, credit_returned = cancel_fulfilment(
            fulfilment,
            actor=self.actor,
            reason="Late customer cancellation",
        )

        self.assertFalse(credit_returned)
        self.assertTrue(cancelled.consumes_entitlement_use)
        self.assertEqual(entitlement.credit_balance, 0)

    def test_subscription_allowance_never_rolls_over(self):
        entitlement = self.create_entitlement(mode=OfferingMode.SUBSCRIPTION, credits=0)
        offering = entitlement.offering
        offering.sessions_per_week = 2
        offering.save(update_fields=("sessions_per_week",))
        entitlement.sessions_per_week_snapshot = 2
        entitlement.save(update_fields=("sessions_per_week_snapshot",))
        today = timezone.localdate()
        monday = today - timedelta(days=today.weekday())
        first_date = max(today, monday) + timedelta(days=1)
        if first_date >= monday + timedelta(days=7):
            first_date = monday + timedelta(days=6)

        request_entitlement_fulfilment(
            entitlement,
            schedule_preference=ServiceSchedulePreference.DATE,
            preferred_date=first_date,
            actor=self.actor,
        )
        request_entitlement_fulfilment(
            entitlement,
            schedule_preference=ServiceSchedulePreference.DATE,
            preferred_date=first_date,
            actor=self.actor,
        )
        with self.assertRaises(ServiceFulfilmentError):
            request_entitlement_fulfilment(
                entitlement,
                schedule_preference=ServiceSchedulePreference.DATE,
                preferred_date=first_date,
                actor=self.actor,
            )

        next_week = monday + timedelta(days=8)
        request_entitlement_fulfilment(
            entitlement,
            schedule_preference=ServiceSchedulePreference.DATE,
            preferred_date=next_week,
            actor=self.actor,
        )
        self.assertEqual(ServiceFulfilment.objects.count(), 3)

    def test_week_specific_staff_adjustment_adds_only_that_weeks_allowance(self):
        entitlement = self.create_entitlement(mode=OfferingMode.SUBSCRIPTION, credits=0)
        entitlement.sessions_per_week_snapshot = 1
        entitlement.save(update_fields=("sessions_per_week_snapshot",))
        service_date = timezone.localdate() + timedelta(days=3)
        week_start = service_date - timedelta(days=service_date.weekday())
        WeeklyUsageAdjustment.objects.create(
            entitlement=entitlement,
            week_start=week_start,
            extra_uses=1,
            reason="Birthday gift",
            created_by=self.actor,
        )

        for _ in range(2):
            request_entitlement_fulfilment(
                entitlement,
                schedule_preference=ServiceSchedulePreference.DATE,
                preferred_date=service_date,
                actor=self.actor,
            )

        with self.assertRaises(ServiceFulfilmentError):
            request_entitlement_fulfilment(
                entitlement,
                schedule_preference=ServiceSchedulePreference.DATE,
                preferred_date=service_date,
                actor=self.actor,
            )

    def test_confirmed_hotel_service_creates_one_request_per_quantity(self):
        booking, stay = create_booking(
            self.customer,
            status=BookingStatus.PAID,
            pet=self.pet,
        )
        service = add_booking_extra(stay, quantity=2)
        service.schedule_preference = ServiceSchedulePreference.DATE_TIME
        service.preferred_date = stay.start_date
        service.preferred_time = time(10, 0)
        service.save(update_fields=("schedule_preference", "preferred_date", "preferred_time"))

        first = activate_confirmed_booking_fulfilments(booking)
        second = activate_confirmed_booking_fulfilments(booking)

        self.assertEqual(len(first), 2)
        self.assertEqual({item.pk for item in first}, {item.pk for item in second})
        self.assertEqual(first[0].preferred_date, stay.start_date)
        self.assertIsNone(first[1].preferred_date)

    def test_operational_closure_requires_explicit_staff_override(self):
        entitlement = self.create_entitlement(credits=1)
        fulfilment = request_entitlement_fulfilment(entitlement, actor=self.actor)
        starts_at = timezone.now() + timedelta(days=3)
        ends_at = starts_at + timedelta(hours=1)
        OperationalClosure.objects.create(
            name="Team holiday",
            starts_at=starts_at - timedelta(hours=1),
            ends_at=ends_at + timedelta(hours=1),
            all_operational_services=True,
            reason="Annual closure",
        )

        with self.assertRaises(ServiceFulfilmentError):
            schedule_fulfilment(
                fulfilment,
                starts_at=starts_at,
                ends_at=ends_at,
                actor=self.actor,
                reason="Manual schedule",
            )

        scheduled = schedule_fulfilment(
            fulfilment,
            starts_at=starts_at,
            ends_at=ends_at,
            actor=self.actor,
            reason="Manager approved exception",
            override_closure=True,
        )
        self.assertEqual(scheduled.status, ServiceFulfilmentStatus.SCHEDULED)
