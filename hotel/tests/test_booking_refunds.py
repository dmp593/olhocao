from decimal import Decimal
from unittest.mock import patch

from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hotel.models import (
    BookingEventType,
    BookingRefund,
    BookingRefundStatus,
    BookingStatus,
    PaymentAttempt,
    PaymentStatus,
)
from hotel.services.payments import PaymentGatewayError, process_payment_notification
from hotel.services.refunds import (
    confirm_booking_refund,
    refundable_booking_balance,
    request_booking_refund,
    retry_booking_refund_request,
)
from hotel.tests.factories import create_account, create_booking


class FakeRefundClient:
    def __init__(self):
        self.requests = []
        self.refund = None
        self.fail_requests = False

    def create_refund(self, provider_capture_id, *, amount, reference):
        self.requests.append((provider_capture_id, amount, reference))
        if self.fail_requests:
            raise PaymentGatewayError("provider unavailable")
        return {"id": f"provider-refund-{len(self.requests)}"}

    def get_refund(self, provider_refund_id):
        return self.refund


class BookingRefundServiceTests(TestCase):
    def setUp(self):
        self.staff = create_account(username="booking-refund-staff", staff=True).user
        self.customer = create_account(username="booking-refund-customer")
        self.booking, _ = create_booking(self.customer, status=BookingStatus.PAID)
        self.payment = PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="refundable-booking-payment",
            provider_payment_id="payment-1",
            provider_capture_id="capture-1",
            amount_eur=self.booking.total_price_eur,
            status=PaymentStatus.PAID,
            paid_at=timezone.now(),
        )
        self.provider = FakeRefundClient()

    def request_refund(self, amount="10.00", *, reason="Customer-care refund"):
        return request_booking_refund(
            self.payment,
            amount=Decimal(amount),
            actor=self.staff,
            reason=reason,
            client=self.provider,
        )

    def provider_confirms(self, refund, *, status="success", amount=None):
        self.provider.refund = {
            "id": refund.provider_refund_id,
            "status": status,
            "transaction_key": refund.refund_reference,
            "value": amount or str(refund.amount_eur),
        }

    def test_multiple_partial_refunds_reserve_exact_remaining_balance(self):
        first = self.request_refund("5.00")
        second_amount = self.payment.amount_eur - Decimal("5.01")
        second = self.request_refund(str(second_amount))

        self.assertEqual(first.status, BookingRefundStatus.PENDING)
        self.assertEqual(second.status, BookingRefundStatus.PENDING)
        self.assertEqual(refundable_booking_balance(self.payment), Decimal("0.01"))
        with self.assertRaisesMessage(PaymentGatewayError, "exceeds the refundable balance"):
            self.request_refund("0.02")

    def test_refund_requires_a_real_confirmed_capture(self):
        self.payment.provider_capture_id = ""
        self.payment.save(update_fields=("provider_capture_id",))

        with self.assertRaisesMessage(PaymentGatewayError, "capture identifier is unavailable"):
            self.request_refund()

        self.assertFalse(BookingRefund.objects.exists())

    def test_reason_is_required_before_reserving_balance(self):
        with self.assertRaisesMessage(PaymentGatewayError, "reason is required"):
            self.request_refund(reason="")

        self.assertFalse(BookingRefund.objects.exists())

    def test_failed_request_retries_with_original_idempotency_reference(self):
        self.provider.fail_requests = True
        with self.assertRaises(PaymentGatewayError):
            self.request_refund("4.00")
        refund = BookingRefund.objects.get()
        original_reference = refund.refund_reference

        self.provider.fail_requests = False
        retried = retry_booking_refund_request(
            refund,
            actor=self.staff,
            client=self.provider,
        )

        self.assertEqual(retried.status, BookingRefundStatus.PENDING)
        self.assertEqual(retried.refund_reference, original_reference)
        self.assertEqual(
            [request[2] for request in self.provider.requests],
            [original_reference, original_reference],
        )

    def test_interrupted_pending_request_is_resubmitted_idempotently(self):
        refund = BookingRefund.objects.create(
            payment_attempt=self.payment,
            amount_eur=Decimal("3.00"),
            status=BookingRefundStatus.PENDING,
            reason="Recover interrupted request",
            refund_reference="booking-refund-recovery-reference",
            requested_by=self.staff,
        )

        result = retry_booking_refund_request(
            refund,
            actor=self.staff,
            client=self.provider,
        )

        self.assertEqual(result.provider_refund_id, "provider-refund-1")
        self.assertEqual(
            self.provider.requests,
            [("capture-1", Decimal("3.00"), "booking-refund-recovery-reference")],
        )

    def test_confirmed_refund_preserves_original_payment_and_issues_correction(self):
        refund = self.request_refund("7.50")
        self.provider_confirms(refund)

        with patch(
            "hotel.services.refunds.issue_booking_refund_credit_note",
            return_value="credit-note-1",
        ) as issue_credit_note:
            confirmed = confirm_booking_refund(
                refund,
                provider_refund_id=refund.provider_refund_id,
                client=self.provider,
            )

        confirmed.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(confirmed.status, BookingRefundStatus.REFUNDED)
        self.assertEqual(self.payment.status, PaymentStatus.PAID)
        self.assertEqual(self.payment.provider_capture_id, "capture-1")
        issue_credit_note.assert_called_once_with(confirmed)
        self.assertTrue(
            self.booking.events.filter(
                event_type=BookingEventType.REFUND_CONFIRMED,
                details__booking_refund_id=confirmed.pk,
            ).exists()
        )

    def test_provider_identity_must_match_before_confirmation(self):
        refund = self.request_refund()
        self.provider.refund = {
            "status": "success",
            "transaction_key": "another-refund",
            "value": str(refund.amount_eur),
        }

        with self.assertRaisesMessage(PaymentGatewayError, "reference does not match"):
            confirm_booking_refund(
                refund,
                provider_refund_id=refund.provider_refund_id,
                client=self.provider,
            )

    def test_commercial_refund_webhook_is_idempotent(self):
        refund = self.request_refund()
        self.provider_confirms(refund)
        payload = {
            "id": refund.provider_refund_id,
            "key": refund.refund_reference,
            "type": "refund",
            "status": "success",
        }

        with patch(
            "hotel.services.refunds.issue_booking_refund_credit_note",
            return_value="credit-note-1",
        ) as issue_credit_note:
            process_payment_notification(payload, client=self.provider)
            process_payment_notification(payload, client=self.provider)

        refund.refresh_from_db()
        self.assertEqual(refund.status, BookingRefundStatus.REFUNDED)
        self.assertEqual(self.payment.events.filter(event_type="booking_refund").count(), 1)
        self.assertEqual(issue_credit_note.call_count, 1)

    def test_financial_refund_record_cannot_be_deleted(self):
        refund = self.request_refund()

        with self.assertRaises(ProtectedError):
            refund.delete()
        with self.assertRaises(ProtectedError):
            BookingRefund.objects.filter(pk=refund.pk).delete()


class BookingRefundViewTests(TestCase):
    def setUp(self):
        self.staff = create_account(username="refund-view-staff", staff=True).user
        self.customer = create_account(username="refund-view-customer")
        self.booking, _ = create_booking(self.customer, status=BookingStatus.PAID)
        self.payment = PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="refund-view-payment",
            provider_capture_id="capture-view",
            amount_eur=self.booking.total_price_eur,
            status=PaymentStatus.PAID,
        )

    def test_customer_cannot_open_staff_refund_form(self):
        self.client.force_login(self.customer.user)

        response = self.client.get(reverse("hotel:booking_refund", args=(self.booking.pk,)))

        self.assertEqual(response.status_code, 404)

    def test_staff_sees_refund_action_and_audited_form(self):
        self.client.force_login(self.staff)

        detail = self.client.get(reverse("hotel:booking_detail", args=(self.booking.pk,)))
        form = self.client.get(reverse("hotel:booking_refund", args=(self.booking.pk,)))

        self.assertContains(detail, "Issue refund")
        self.assertContains(form, "Reason for the refund")
        self.assertContains(form, f'value="{self.booking.total_price_eur}"')

    @patch("hotel.booking_views.request_booking_refund")
    def test_staff_can_request_partial_refund(self, request_refund):
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("hotel:booking_refund", args=(self.booking.pk,)),
            {"amount_eur": "5.00", "reason": "Complaint resolution"},
        )

        self.assertRedirects(
            response,
            reverse("hotel:booking_detail", args=(self.booking.pk,)),
        )
        request_refund.assert_called_once_with(
            self.payment,
            amount=Decimal("5.00"),
            actor=self.staff,
            reason="Complaint resolution",
        )

    @patch("hotel.booking_views.toconline_gateway.download_sales_document")
    def test_customer_can_download_own_confirmed_credit_note(self, download):
        download.return_value = b"%PDF-test"
        refund = BookingRefund.objects.create(
            payment_attempt=self.payment,
            amount_eur=Decimal("5.00"),
            status=BookingRefundStatus.REFUNDED,
            reason="Customer care",
            refund_reference="download-booking-credit-note",
            provider_refund_id="provider-download-refund",
            toconline_amend_document_id="credit-document-1",
            refunded_at=timezone.now(),
        )
        self.client.force_login(self.customer.user)

        response = self.client.get(
            reverse(
                "hotel:booking_refund_pdf",
                args=(self.booking.pk, refund.pk),
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        download.assert_called_once_with("credit-document-1")
