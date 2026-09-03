from datetime import timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from activities.models import (
    ActivityReservation,
    ActivitySessionEvent,
    CreditLedgerEntry,
    EntitlementStatus,
    LedgerReason,
    OfferingMode,
    ReservationStatus,
)
from activities.services.reservations import (
    ActivityReservationError,
    cancel_activity_session,
    cancel_reservation,
    expire_finished_entitlements,
    reserve_session,
)
from activities.tests.factories import create_offering, create_session
from hotel.tests.factories import create_account, create_pet


class ReservationRulesTests(TestCase):
    def setUp(self):
        self.customer = create_account()
        self.pet = create_pet(self.customer)

    def create_active_entitlement(self, offering, *, credits=0):
        entitlement = offering.entitlements.create(
            customer=self.customer,
            pet=self.pet,
            status=EntitlementStatus.ACTIVE,
            item_name_snapshot=offering.catalog_item.name,
            unit_price_eur_snapshot=offering.price_eur,
            merchant_reference=f"activity-test-{offering.pk}",
            current_period_end=timezone.now() + timedelta(days=60),
        )
        if credits:
            CreditLedgerEntry.objects.create(
                entitlement=entitlement,
                delta=credits,
                reason=LedgerReason.PURCHASE,
                idempotency_key=f"initial-{entitlement.pk}",
            )
        return entitlement

    def test_pack_reservation_consumes_one_credit(self):
        entitlement = self.create_active_entitlement(create_offering(), credits=2)
        session = create_session()

        reserve_session(entitlement=entitlement, session=session, actor=self.customer.user)

        self.assertEqual(entitlement.credit_balance, 1)

    def test_attended_reservation_cannot_be_booked_twice(self):
        entitlement = self.create_active_entitlement(create_offering(), credits=2)
        session = create_session()
        reservation = reserve_session(
            entitlement=entitlement,
            session=session,
            actor=self.customer.user,
        )
        reservation.status = ReservationStatus.ATTENDED
        reservation.save(update_fields=("status",))

        duplicate = reserve_session(
            entitlement=entitlement,
            session=session,
            actor=self.customer.user,
        )

        self.assertEqual(duplicate, reservation)
        self.assertEqual(ActivityReservation.objects.count(), 1)
        self.assertEqual(entitlement.credit_balance, 1)

    def test_early_cancellation_returns_pack_credit(self):
        entitlement = self.create_active_entitlement(create_offering(), credits=1)
        reservation = reserve_session(
            entitlement=entitlement,
            session=create_session(starts_in_days=3),
            actor=self.customer.user,
        )

        _, credit_returned = cancel_reservation(reservation, actor=self.customer.user)

        self.assertTrue(credit_returned)
        self.assertEqual(entitlement.credit_balance, 1)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_cancelling_a_session_releases_reservations_credits_and_notifies_customers(self):
        entitlement = self.create_active_entitlement(create_offering(), credits=1)
        session = create_session(starts_in_days=3)
        reservation = reserve_session(
            entitlement=entitlement,
            session=session,
            actor=self.customer.user,
        )

        with self.captureOnCommitCallbacks(execute=True):
            cancelled_session, cancelled_count = cancel_activity_session(
                session,
                actor=self.customer.user,
                reason="Encerramento excecional do centro",
            )

        reservation.refresh_from_db()
        self.assertTrue(cancelled_session.is_cancelled)
        self.assertEqual(cancelled_count, 1)
        self.assertEqual(reservation.status, ReservationStatus.CANCELLED)
        self.assertTrue(reservation.cancellation_policy_overridden)
        self.assertEqual(entitlement.credit_balance, 1)
        event = ActivitySessionEvent.objects.get(session=session, action="cancelled")
        self.assertEqual(event.details["reservations_cancelled"], 1)
        self.assertEqual(event.details["pack_credits_returned"], 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(session.title, mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, [self.customer.user.email])

    def test_cancelling_a_session_is_idempotent(self):
        session = create_session(starts_in_days=3)

        cancel_activity_session(
            session,
            actor=self.customer.user,
            reason="Primeiro cancelamento",
        )
        _, cancelled_count = cancel_activity_session(
            session,
            actor=self.customer.user,
            reason="Pedido repetido",
        )

        self.assertEqual(cancelled_count, 0)
        self.assertEqual(
            ActivitySessionEvent.objects.filter(session=session, action="cancelled").count(),
            1,
        )

    def test_started_session_cannot_be_cancelled(self):
        session = create_session(starts_in_days=-1)

        with self.assertRaisesMessage(ActivityReservationError, "has started"):
            cancel_activity_session(
                session,
                actor=self.customer.user,
                reason="Tentativa tardia",
            )

    def test_service_level_cannot_book_session_for_another_level(self):
        entitlement = self.create_active_entitlement(create_offering(), credits=1)
        session = create_session(eligible_level_names=("Priority",))

        with self.assertRaises(ActivityReservationError):
            reserve_session(entitlement=entitlement, session=session, actor=self.customer.user)

    def test_session_access_is_independent_from_commercial_names(self):
        offering = create_offering(level_name="Priority")
        offering.catalog_item.name = "GOLD FreePass"
        offering.catalog_item.save(update_fields=("name",))
        offering.catalog_level.name = "Anything the manager chooses"
        offering.catalog_level.save(update_fields=("name",))
        entitlement = self.create_active_entitlement(offering, credits=1)

        reservation = reserve_session(
            entitlement=entitlement,
            session=create_session(eligible_levels=(offering.catalog_level,)),
            actor=self.customer.user,
        )

        self.assertEqual(reservation.entitlement, entitlement)

    def test_session_capacity_is_enforced(self):
        offering = create_offering()
        first = self.create_active_entitlement(offering, credits=1)
        session = create_session(capacity=1)
        reserve_session(entitlement=first, session=session, actor=self.customer.user)
        second_customer = create_account(username="second")
        second_pet = create_pet(second_customer, name="Luna")
        second = offering.entitlements.create(
            customer=second_customer,
            pet=second_pet,
            status=EntitlementStatus.ACTIVE,
            item_name_snapshot=offering.catalog_item.name,
            unit_price_eur_snapshot=offering.price_eur,
            merchant_reference="activity-second",
            current_period_end=timezone.now() + timedelta(days=60),
        )
        CreditLedgerEntry.objects.create(
            entitlement=second,
            delta=1,
            reason=LedgerReason.PURCHASE,
            idempotency_key="initial-second",
        )

        with self.assertRaises(ActivityReservationError):
            reserve_session(entitlement=second, session=session, actor=second_customer.user)

    def test_subscription_weekly_limit_is_enforced(self):
        offering = create_offering(mode=OfferingMode.SUBSCRIPTION)
        offering.sessions_per_week = 1
        offering.save(update_fields=("sessions_per_week",))
        entitlement = self.create_active_entitlement(offering)
        first_session = create_session(starts_in_days=3)
        second_session = create_session(starts_in_days=3)
        second_session.starts_at = first_session.starts_at + timedelta(hours=1)
        second_session.ends_at = second_session.starts_at + timedelta(hours=2)
        second_session.save()
        reserve_session(
            entitlement=entitlement,
            session=first_session,
            actor=self.customer.user,
        )

        with self.assertRaises(ActivityReservationError):
            reserve_session(
                entitlement=entitlement,
                session=second_session,
                actor=self.customer.user,
            )
        self.assertEqual(ActivityReservation.objects.count(), 1)

    def test_session_after_paid_access_period_is_rejected(self):
        entitlement = self.create_active_entitlement(create_offering(), credits=1)
        session = create_session(starts_in_days=70)

        with self.assertRaisesMessage(ActivityReservationError, "outside the purchased"):
            reserve_session(
                entitlement=entitlement,
                session=session,
                actor=self.customer.user,
            )

        self.assertEqual(entitlement.credit_balance, 1)

    def test_session_before_paid_access_period_is_rejected(self):
        entitlement = self.create_active_entitlement(create_offering(), credits=1)
        entitlement.current_period_start = timezone.now() + timedelta(days=10)
        entitlement.save(update_fields=("current_period_start", "updated_at"))
        session = create_session(starts_in_days=3)

        with self.assertRaisesMessage(ActivityReservationError, "before the purchased"):
            reserve_session(
                entitlement=entitlement,
                session=session,
                actor=self.customer.user,
            )

        self.assertEqual(entitlement.credit_balance, 1)

    def test_ended_subscription_becomes_past_due_without_allowing_a_duplicate(self):
        offering = create_offering(mode=OfferingMode.SUBSCRIPTION)
        entitlement = self.create_active_entitlement(offering)
        entitlement.current_period_end = timezone.now() - timedelta(minutes=1)
        entitlement.save(update_fields=("current_period_end", "updated_at"))

        changed_count = expire_finished_entitlements()

        entitlement.refresh_from_db()
        self.assertEqual(changed_count, 1)
        self.assertEqual(entitlement.status, EntitlementStatus.PAST_DUE)
        self.assertFalse(entitlement.has_access)
        self.assertIn("No successful renewal", entitlement.last_payment_error)

    def test_ended_pack_expires(self):
        entitlement = self.create_active_entitlement(create_offering(), credits=1)
        entitlement.current_period_end = timezone.now() - timedelta(minutes=1)
        entitlement.save(update_fields=("current_period_end", "updated_at"))

        changed_count = expire_finished_entitlements()

        entitlement.refresh_from_db()
        self.assertEqual(changed_count, 1)
        self.assertEqual(entitlement.status, EntitlementStatus.EXPIRED)
        self.assertFalse(entitlement.has_access)

    def test_past_due_subscription_stays_open_for_provider_recovery(self):
        offering = create_offering(mode=OfferingMode.SUBSCRIPTION)
        entitlement = self.create_active_entitlement(offering)
        entitlement.status = EntitlementStatus.PAST_DUE
        entitlement.current_period_end = timezone.now() - timedelta(minutes=1)
        entitlement.save(update_fields=("status", "current_period_end", "updated_at"))

        changed_count = expire_finished_entitlements()

        entitlement.refresh_from_db()
        self.assertEqual(changed_count, 0)
        self.assertEqual(entitlement.status, EntitlementStatus.PAST_DUE)
