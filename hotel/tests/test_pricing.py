from decimal import Decimal
from unittest.mock import patch

from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse

from discounts.models import (
    DiscountSource,
    DiscountType,
    Promotion,
    RedemptionStatus,
    SaleChannel,
)
from discounts.services import PromotionUnavailable
from hotel.models import (
    BookingDocumentStatus,
    BookingEventType,
    BookingStatus,
    PaymentAttempt,
    PaymentStatus,
)
from hotel.services.payments import PaymentGatewayError
from hotel.services.pricing import (
    BookingPriceChangeError,
    apply_promotion_to_booking,
    booking_purchase_reference,
    set_booking_final_price,
)
from hotel.tests.factories import (
    add_booking_extra,
    create_account,
    create_booking,
)


class RecordingPaymentLinkClient:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.cancelled_link_ids = []

    def cancel_payment_link(self, link_id):
        if self.fail:
            raise PaymentGatewayError("provider unavailable")
        self.cancelled_link_ids.append(link_id)


class BookingPromotionTests(TestCase):
    def setUp(self):
        self.customer = create_account(username="booking-promotion-customer")
        self.booking, self.stay = create_booking(self.customer, duration=2)
        self.extra = add_booking_extra(self.stay, price="5.00", quantity=2)
        self.promotion = Promotion.objects.create(
            code="CARE10",
            name="Care discount",
            discount_type=DiscountType.PERCENTAGE,
            value=Decimal("10"),
            eligible_sale_channels=[SaleChannel.DATE_RANGE, SaleChannel.ADD_ON],
        )

    def test_promotion_is_snapshotted_and_allocated_to_fiscal_lines(self):
        quote = apply_promotion_to_booking(
            self.booking,
            "care10",
            actor=self.customer.user,
        )

        self.booking.refresh_from_db()
        self.stay.refresh_from_db()
        self.extra.refresh_from_db()
        self.assertEqual(self.booking.gross_total_price_eur, Decimal("50.00"))
        self.assertEqual(self.booking.total_price_eur, Decimal("45.00"))
        self.assertEqual(quote.discount_amount_eur, Decimal("5.00"))
        self.assertEqual(self.booking.discount_source, DiscountSource.PROMOTION)
        self.assertEqual(self.booking.promotion_code_snapshot, "CARE10")
        self.assertEqual(
            self.stay.discount_amount_eur_snapshot + self.extra.discount_amount_eur_snapshot,
            Decimal("5.00"),
        )
        redemption = self.promotion.redemptions.get()
        self.assertEqual(redemption.status, RedemptionStatus.RESERVED)
        self.assertEqual(redemption.purchase_reference, booking_purchase_reference(self.booking))

    def test_full_discount_confirms_without_creating_a_fake_payment(self):
        self.promotion.discount_type = DiscountType.PERCENTAGE
        self.promotion.value = Decimal("100")
        self.promotion.save(update_fields=("discount_type", "value", "updated_at"))

        apply_promotion_to_booking(self.booking, "CARE10", actor=self.customer.user)

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.COMPLIMENTARY)
        self.assertEqual(self.booking.total_price_eur, Decimal("0.00"))
        self.assertIsNotNone(self.booking.confirmed_at)
        self.assertIsNone(self.booking.paid_at)
        self.assertFalse(self.booking.payment_attempts.exists())
        self.assertEqual(
            self.promotion.redemptions.get().status,
            RedemptionStatus.CONSUMED,
        )

    def test_promotion_cannot_leave_an_unpayable_non_zero_total(self):
        self.promotion.discount_type = DiscountType.FIXED
        self.promotion.value = Decimal("49.75")
        self.promotion.save(update_fields=("discount_type", "value", "updated_at"))

        with self.assertRaisesMessage(PromotionUnavailable, "minimum online payment"):
            apply_promotion_to_booking(self.booking, "CARE10", actor=self.customer.user)

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.total_price_eur, Decimal("50.00"))
        self.assertFalse(self.promotion.redemptions.exists())


class BookingStaffPriceAdjustmentTests(TestCase):
    def setUp(self):
        self.customer = create_account(username="price-customer")
        self.staff = create_account(username="price-staff", staff=True)
        self.booking, self.stay = create_booking(self.customer, duration=2)
        self.extra = add_booking_extra(self.stay, price="5.00", quantity=2)

    def adjust(self, final_price, *, client=None, reason="Customer care gesture"):
        return set_booking_final_price(
            self.booking,
            final_price_eur=final_price,
            reason=reason,
            actor=self.staff.user,
            payment_client=client,
        )

    def test_staff_can_confirm_a_free_booking_with_a_reason_and_audit_history(self):
        payment = PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="old-price-link",
            provider_link_id="link-old-price",
            payment_url="https://pay.example/old-price",
            amount_eur=Decimal("50.00"),
        )
        client = RecordingPaymentLinkClient()

        self.adjust(Decimal("0.00"), client=client)

        self.booking.refresh_from_db()
        self.stay.refresh_from_db()
        self.extra.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(client.cancelled_link_ids, ["link-old-price"])
        self.assertEqual(payment.status, PaymentStatus.CANCELLED)
        self.assertEqual(self.booking.status, BookingStatus.COMPLIMENTARY)
        self.assertEqual(self.booking.discount_source, DiscountSource.STAFF)
        self.assertEqual(self.booking.discount_amount_eur_snapshot, Decimal("50.00"))
        self.assertEqual(self.booking.total_price_eur, Decimal("0.00"))
        self.assertEqual(
            self.stay.discount_amount_eur_snapshot + self.extra.discount_amount_eur_snapshot,
            Decimal("50.00"),
        )
        adjustment = self.booking.price_adjustments.get()
        self.assertEqual(adjustment.previous_total_amount_eur, Decimal("50.00"))
        self.assertEqual(adjustment.new_total_amount_eur, Decimal("0.00"))
        self.assertEqual(adjustment.actor, self.staff.user)
        self.assertTrue(
            self.booking.events.filter(event_type=BookingEventType.COMPLIMENTARY_CONFIRMED).exists()
        )
        with self.assertRaises(ProtectedError):
            adjustment.delete()

    def test_staff_can_restore_a_complimentary_booking_to_catalog_price(self):
        self.adjust(Decimal("0.00"))
        self.booking.refresh_from_db()

        self.adjust(Decimal("50.00"), reason="Customer requested normal payment")

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.PENDING)
        self.assertEqual(self.booking.total_price_eur, Decimal("50.00"))
        self.assertEqual(self.booking.discount_source, DiscountSource.NONE)
        self.assertEqual(self.booking.discount_amount_eur_snapshot, Decimal("0.00"))
        self.assertEqual(self.booking.price_adjustments.count(), 2)

    def test_existing_link_failure_rolls_back_the_price_change(self):
        payment = PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="link-that-cannot-be-disabled",
            provider_link_id="provider-link",
            amount_eur=Decimal("50.00"),
        )

        with self.assertRaisesMessage(BookingPriceChangeError, "could not be disabled"):
            self.adjust(Decimal("25.00"), client=RecordingPaymentLinkClient(fail=True))

        self.booking.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(self.booking.total_price_eur, Decimal("50.00"))
        self.assertEqual(payment.status, PaymentStatus.PENDING)
        self.assertFalse(self.booking.price_adjustments.exists())

    def test_manual_adjustment_releases_an_unpaid_promotion_use(self):
        promotion = Promotion.objects.create(
            code="ONEUSE",
            name="One use",
            discount_type=DiscountType.FIXED,
            value=Decimal("5.00"),
            eligible_sale_channels=[SaleChannel.DATE_RANGE],
            max_redemptions=1,
        )
        apply_promotion_to_booking(self.booking, promotion.code, actor=self.customer.user)

        self.adjust(Decimal("20.00"))

        redemption = promotion.redemptions.get()
        self.assertEqual(redemption.status, RedemptionStatus.RELEASED)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.discount_source, DiscountSource.STAFF)
        self.assertEqual(self.booking.promotion_code_snapshot, "")

    def test_customer_cannot_change_a_booking_price(self):
        with self.assertRaisesMessage(BookingPriceChangeError, "Only staff"):
            set_booking_final_price(
                self.booking,
                final_price_eur=Decimal("0.00"),
                reason="Trying to discount own booking",
                actor=self.customer.user,
            )

    def test_staff_must_choose_zero_or_a_provider_payable_amount(self):
        with self.assertRaisesMessage(BookingPriceChangeError, "at least 0.50"):
            self.adjust(Decimal("0.25"))

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.total_price_eur, Decimal("50.00"))
        self.assertFalse(self.booking.price_adjustments.exists())

    def test_captured_payment_locks_the_price(self):
        PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="captured-price",
            provider_capture_id="capture-1",
            amount_eur=Decimal("50.00"),
            status=PaymentStatus.PAID,
        )

        with self.assertRaisesMessage(BookingPriceChangeError, "payment has already been captured"):
            self.adjust(Decimal("0.00"))

    def test_issued_fiscal_document_locks_the_price(self):
        self.booking.document_status = BookingDocumentStatus.ISSUED
        self.booking.toconline_sale_document_id = "document-1"
        self.booking.save(
            update_fields=("document_status", "toconline_sale_document_id", "updated_at")
        )

        with self.assertRaisesMessage(BookingPriceChangeError, "fiscal document"):
            self.adjust(Decimal("0.00"))


class BookingPriceAdjustmentViewTests(TestCase):
    def setUp(self):
        self.customer = create_account(username="price-view-customer")
        self.staff = create_account(username="price-view-staff", staff=True)
        self.booking, _stay = create_booking(self.customer, duration=1)
        self.url = reverse(
            "hotel:booking_price_adjustment",
            kwargs={"booking_id": self.booking.pk},
        )

    def test_customer_cannot_open_the_staff_price_form(self):
        self.client.force_login(self.customer.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)

    def test_staff_can_make_the_booking_complimentary(self):
        self.client.force_login(self.staff.user)

        response = self.client.post(
            self.url,
            {
                "final_price_eur": "0.00",
                "reason": "Complimentary stay after complaint",
            },
        )

        self.assertRedirects(
            response,
            reverse("hotel:booking_detail", kwargs={"pk": self.booking.pk}),
        )
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.COMPLIMENTARY)
        self.assertContains(
            self.client.get(reverse("hotel:booking_detail", kwargs={"pk": self.booking.pk})),
            "Complimentary booking",
        )

    def test_provider_failure_is_explained_without_changing_the_price(self):
        PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="price-form-provider-failure",
            provider_link_id="remote-link",
            amount_eur=self.booking.total_price_eur,
        )
        self.client.force_login(self.staff.user)

        with patch(
            "hotel.services.pricing.EasypayClient.cancel_payment_link",
            side_effect=PaymentGatewayError("provider unavailable"),
        ):
            response = self.client.post(
                self.url,
                {
                    "final_price_eur": "0.00",
                    "reason": "Complimentary stay after complaint",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The price could not be changed")
        self.assertContains(response, "existing payment link could not be disabled")
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.PENDING)
        self.assertEqual(self.booking.total_price_eur, self.booking.gross_total_price_eur)
