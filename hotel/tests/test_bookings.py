from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, override_settings

from activities.models import OfferingMode, ServiceOffering
from catalog.models import AddOnAvailability
from hotel.models import (
    Booking,
    BookingRefundStatus,
    BookingStatus,
    CatalogBookingKind,
    PaymentAttempt,
    PaymentStatus,
)
from hotel.services.bookings import (
    BookingValidationError,
    build_booking_review,
    cancel_booking,
    create_booking,
    ensure_stay_capacity_available,
)
from hotel.services.payments import PaymentGatewayError
from hotel.tests.factories import (
    add_booking_extra,
    create_account,
    create_catalog_item,
    create_catalog_offer,
    create_pet,
)
from hotel.tests.factories import (
    create_booking as create_booking_record,
)


class BookingCreationTests(TestCase):
    def setUp(self):
        self.account = create_account()
        self.pet = create_pet(self.account)
        self.stay = create_catalog_item(price="20.00")
        self.extra = create_catalog_item(
            remote_id="extra-1",
            name="Walk",
            price="5.00",
            booking_kind=CatalogBookingKind.EXTRA,
        )
        self.stay_offer = create_catalog_offer(self.stay)
        self.extra_offer = create_catalog_offer(self.extra)
        ServiceOffering.objects.create(
            catalog_offer=self.extra_offer,
            mode=OfferingMode.ONE_OFF,
            cancellation_notice_hours=24,
            is_published=True,
        )
        AddOnAvailability.objects.create(
            add_on=self.extra_offer,
            target_type=self.stay_offer.catalog_type,
        )
        self.booking_data = {
            "offer_id": str(self.stay_offer.pk),
            "pet_ids": [str(self.pet.pk)],
            "start_date": (date.today() + timedelta(days=14)).isoformat(),
            "end_date": (date.today() + timedelta(days=17)).isoformat(),
            "pets_services": {
                str(self.pet.pk): {
                    str(self.extra_offer.pk): {
                        "quantity": 2,
                        "schedule_preference": "unscheduled",
                    }
                }
            },
        }

    def test_review_calculates_stay_and_per_pet_extras(self):
        second_pet = create_pet(self.account, name="Luna")
        self.booking_data["pet_ids"].append(str(second_pet.pk))
        self.booking_data["pets_services"][str(second_pet.pk)] = {
            str(self.extra_offer.pk): {
                "quantity": 1,
                "schedule_preference": "unscheduled",
            }
        }

        review = build_booking_review(self.account, self.booking_data)

        self.assertEqual(review.duration, 3)
        self.assertEqual(review.pricing["stay"]["total_price"], Decimal("120.00"))
        self.assertEqual(review.pricing["grand_total"], Decimal("135.00"))

    def test_create_booking_persists_immutable_commercial_snapshots(self):
        booking = create_booking(self.account, self.booking_data, notes="Quiet room")

        stay = booking.stays.get()
        service = stay.services.get()
        self.assertEqual(booking.total_price_eur, Decimal("70.00"))
        self.assertEqual(stay.item_name_snapshot, "Hotel")
        self.assertEqual(stay.unit_price_eur_snapshot, Decimal("20.00"))
        self.assertEqual(service.item_name_snapshot, "Walk")
        self.assertEqual(service.unit_price_eur_snapshot, Decimal("5.00"))
        self.assertEqual(booking.notes, "Quiet room")

        self.stay.unit_price_eur = Decimal("99.00")
        self.stay.name = "Renamed"
        self.stay.save(update_fields=("unit_price_eur", "name"))
        self.assertEqual(booking.total_price_eur, Decimal("70.00"))
        self.assertEqual(stay.stay_name, "Hotel")

    def test_rejects_pet_owned_by_another_account(self):
        foreign_pet = create_pet(create_account(username="other"))
        self.booking_data["pet_ids"] = [str(foreign_pet.pk)]
        self.booking_data["pets_services"] = {}

        with self.assertRaises(BookingValidationError):
            build_booking_review(self.account, self.booking_data)

    def test_rejects_service_quantity_longer_than_stay(self):
        self.booking_data["pets_services"][str(self.pet.pk)][str(self.extra_offer.pk)][
            "quantity"
        ] = 4

        with self.assertRaises(BookingValidationError):
            build_booking_review(self.account, self.booking_data)

    def test_rejects_inactive_or_deleted_catalog_items(self):
        self.stay.is_active = False
        self.stay.save(update_fields=("is_active",))

        with self.assertRaises(BookingValidationError):
            build_booking_review(self.account, self.booking_data)

    def test_rejects_past_or_zero_length_dates(self):
        self.booking_data["start_date"] = (date.today() - timedelta(days=1)).isoformat()
        with self.assertRaises(BookingValidationError):
            build_booking_review(self.account, self.booking_data)

        self.booking_data["start_date"] = self.booking_data["end_date"]
        with self.assertRaises(BookingValidationError):
            build_booking_review(self.account, self.booking_data)


class BookingStateTests(TestCase):
    def test_empty_booking_is_never_modifiable_or_upcoming(self):
        account = create_account()
        booking = Booking.objects.create(customer=account, status=BookingStatus.PAID)

        self.assertFalse(booking.can_modify)
        self.assertFalse(booking.can_cancel)
        self.assertFalse(booking.is_upcoming)

    def test_seven_day_policy_boundary_is_allowed(self):
        account = create_account()
        booking, _ = create_booking_record(account, start_in_days=7)

        self.assertTrue(booking.can_modify)
        self.assertTrue(booking.can_cancel)

    def test_six_day_policy_boundary_is_rejected(self):
        account = create_account()
        booking, _ = create_booking_record(account, start_in_days=6)

        self.assertFalse(booking.can_modify)
        self.assertFalse(booking.can_cancel)


class BookingCapacityTests(TestCase):
    def test_equal_date_ranges_count_every_dog_against_capacity(self):
        account = create_account(username="capacity-customer")
        stay_item = create_catalog_item(remote_id="capacity-stay")
        stay_offer = create_catalog_offer(stay_item, capacity=2)
        start_date = date.today() + timedelta(days=14)
        end_date = start_date + timedelta(days=3)

        create_booking_record(
            account,
            stay_item=stay_item,
            pet=create_pet(account, name="Rex"),
            duration=3,
        )
        create_booking_record(
            account,
            stay_item=stay_item,
            pet=create_pet(account, name="Luna"),
            duration=3,
        )

        with self.assertRaisesMessage(BookingValidationError, "accommodation is full"):
            ensure_stay_capacity_available(
                stay_offer,
                start_date=start_date,
                end_date=end_date,
                requested_dogs=1,
            )


class FakeCancellationClient:
    def __init__(self, *, fail_refund=False):
        self.fail_refund = fail_refund
        self.cancelled_links = []
        self.refunds = []

    def cancel_payment_link(self, link_id):
        self.cancelled_links.append(link_id)

    def create_refund(self, capture_id, *, amount, reference):
        if self.fail_refund:
            raise PaymentGatewayError("temporary refund failure")
        self.refunds.append((capture_id, amount, reference))
        return {"id": "refund-1"}


class BookingCancellationTests(TestCase):
    def test_pending_booking_disables_payment_link(self):
        account = create_account()
        booking, _ = create_booking_record(account)
        payment = PaymentAttempt.objects.create(
            booking=booking,
            merchant_reference="booking-pending",
            provider_link_id="link-1",
            payment_url="https://pay.example/link-1",
            amount_eur=booking.total_price_eur,
        )
        client = FakeCancellationClient()

        result = cancel_booking(booking, payment_client=client)

        booking.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CANCELLED)
        self.assertEqual(payment.status, PaymentStatus.CANCELLED)
        self.assertEqual(client.cancelled_links, ["link-1"])
        self.assertFalse(result.refund_requested)

    @override_settings(HOTEL_REFUND_PERCENTAGE=0.85)
    def test_paid_booking_requests_configured_partial_refund(self):
        account = create_account()
        booking, stay = create_booking_record(
            account,
            status=BookingStatus.PAID,
            duration=3,
        )
        add_booking_extra(stay, price="5.00", quantity=2)
        payment = PaymentAttempt.objects.create(
            booking=booking,
            merchant_reference="booking-paid",
            provider_payment_id="single-1",
            provider_capture_id="capture-1",
            amount_eur=Decimal("70.00"),
            status=PaymentStatus.PAID,
        )
        client = FakeCancellationClient()

        result = cancel_booking(booking, payment_client=client)

        booking.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CANCELLED)
        self.assertEqual(payment.status, PaymentStatus.PAID)
        refund = payment.booking_refunds.get()
        self.assertEqual(refund.status, BookingRefundStatus.PENDING)
        self.assertEqual(refund.amount_eur, Decimal("59.50"))
        self.assertEqual(client.refunds[0][0], "capture-1")
        self.assertTrue(result.refund_requested)

    @override_settings(HOTEL_REFUND_PERCENTAGE=0.85)
    def test_refund_failure_does_not_restore_cancelled_booking(self):
        account = create_account()
        booking, _ = create_booking_record(account, status=BookingStatus.PAID)
        payment = PaymentAttempt.objects.create(
            booking=booking,
            merchant_reference="booking-failed-refund",
            provider_capture_id="capture-1",
            amount_eur=booking.total_price_eur,
            status=PaymentStatus.PAID,
        )

        result = cancel_booking(
            booking,
            payment_client=FakeCancellationClient(fail_refund=True),
        )

        booking.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CANCELLED)
        self.assertEqual(payment.status, PaymentStatus.PAID)
        self.assertEqual(
            payment.booking_refunds.get().status,
            BookingRefundStatus.FAILED,
        )
        self.assertIn("temporary", result.refund_error)

    def test_cannot_cancel_inside_policy_window(self):
        account = create_account()
        booking, _ = create_booking_record(account, start_in_days=2)

        with self.assertRaises(BookingValidationError):
            cancel_booking(booking, payment_client=FakeCancellationClient())

    def test_staff_override_can_cancel_inside_customer_policy_window(self):
        account = create_account()
        booking, _ = create_booking_record(account, start_in_days=2)

        cancel_booking(
            booking,
            payment_client=FakeCancellationClient(),
            enforce_customer_policy=False,
        )

        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CANCELLED)
