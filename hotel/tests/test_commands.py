from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from hotel.models import (
    BookingDocumentStatus,
    BookingEventType,
    BookingRefund,
    BookingRefundStatus,
    BookingStatus,
    PaymentAttempt,
    PaymentStatus,
)
from hotel.tests.factories import create_account, create_booking, create_catalog_item


class PaymentRecoveryCommandTests(TestCase):
    def setUp(self):
        self.booking, _ = create_booking(
            create_account(),
            status=BookingStatus.PAID,
        )
        self.booking.paid_at = timezone.now() - timedelta(minutes=5)
        self.booking.toconline_sale_document_id = "sale-document-1"
        self.booking.document_status = BookingDocumentStatus.ISSUED
        self.booking.save(
            update_fields=(
                "paid_at",
                "toconline_sale_document_id",
                "document_status",
                "updated_at",
            )
        )

    @patch("hotel.management.commands.retry_easypay_refunds.request_refund")
    def test_failed_duplicate_payment_refund_is_retried_for_a_paid_booking(
        self,
        request_refund,
    ):
        payment = PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="duplicate-payment-refund",
            provider_capture_id="duplicate-capture",
            amount_eur=Decimal("20.00"),
            status=PaymentStatus.REFUND_FAILED,
            refund_amount_eur=Decimal("20.00"),
            refund_reference="duplicate-refund-reference",
        )

        call_command("retry_easypay_refunds", stdout=StringIO())

        request_refund.assert_called_once_with(payment, Decimal("20.00"))

    @patch("hotel.management.commands.retry_easypay_refunds.reconcile_pending_refund")
    def test_pending_refund_is_reconciled(self, reconcile_pending_refund):
        payment = PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="missed-refund-webhook",
            provider_capture_id="capture-missed-webhook",
            provider_refund_id="refund-missed-webhook",
            amount_eur=Decimal("20.00"),
            status=PaymentStatus.REFUND_PENDING,
            refund_amount_eur=Decimal("17.00"),
            refund_reference="missed-refund-reference",
        )
        payment.status = PaymentStatus.REFUNDED
        reconcile_pending_refund.return_value = payment

        call_command("retry_easypay_refunds", stdout=StringIO())

        reconcile_pending_refund.assert_called_once()

    @patch("hotel.management.commands.retry_easypay_refunds.request_refund")
    def test_interrupted_pending_refund_request_is_resubmitted(self, request_refund):
        payment = PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="interrupted-refund-request",
            provider_capture_id="capture-interrupted-refund",
            amount_eur=Decimal("20.00"),
            status=PaymentStatus.REFUND_PENDING,
            refund_amount_eur=Decimal("17.00"),
            refund_reference="interrupted-refund-reference",
            provider_refund_id="",
        )

        call_command("retry_easypay_refunds", stdout=StringIO())

        request_refund.assert_called_once_with(payment, Decimal("17.00"))

    @patch("hotel.management.commands.retry_easypay_refunds.retry_booking_refund_request")
    def test_interrupted_commercial_booking_refund_is_resubmitted(self, retry_refund):
        payment = PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="commercial-booking-refund-payment",
            provider_capture_id="commercial-capture",
            amount_eur=self.booking.total_price_eur,
            status=PaymentStatus.PAID,
        )
        refund = BookingRefund.objects.create(
            payment_attempt=payment,
            amount_eur=Decimal("5.00"),
            status=BookingRefundStatus.PENDING,
            reason="Recover interrupted commercial refund",
            refund_reference="commercial-refund-recovery",
        )

        call_command("retry_easypay_refunds", stdout=StringIO())

        retry_refund.assert_called_once_with(refund)

    @patch("hotel.management.commands.retry_toconline_documents.issue_booking_document")
    @patch(
        "hotel.management.commands.retry_toconline_documents.issue_confirmed_booking_credit_note"
    )
    def test_pending_refund_does_not_create_a_credit_note(
        self,
        issue_credit_note,
        issue_document,
    ):
        PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="pending-refund",
            provider_capture_id="pending-capture",
            amount_eur=Decimal("20.00"),
            status=PaymentStatus.REFUND_PENDING,
            refund_amount_eur=Decimal("17.00"),
            refund_reference="pending-refund-reference",
        )

        call_command("retry_toconline_documents", stdout=StringIO())

        issue_document.assert_not_called()
        issue_credit_note.assert_not_called()

    @patch("hotel.management.commands.retry_toconline_documents.issue_booking_document")
    @patch(
        "hotel.management.commands.retry_toconline_documents.issue_confirmed_booking_credit_note"
    )
    def test_confirmed_refund_retries_its_credit_note(
        self,
        issue_credit_note,
        issue_document,
    ):
        payment = PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="confirmed-refund",
            provider_capture_id="confirmed-capture",
            amount_eur=Decimal("20.00"),
            status=PaymentStatus.REFUNDED,
            refund_amount_eur=Decimal("17.00"),
            refund_reference="confirmed-refund-reference",
        )

        call_command("retry_toconline_documents", stdout=StringIO())

        issue_document.assert_called_once_with(self.booking)
        issue_credit_note.assert_called_once_with(payment)

    @patch("hotel.management.commands.retry_toconline_documents.issue_booking_refund_credit_note")
    def test_confirmed_commercial_booking_refund_retries_its_own_credit_note(
        self,
        issue_credit_note,
    ):
        payment = PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="commercial-refund-document-payment",
            provider_capture_id="commercial-refund-document-capture",
            amount_eur=self.booking.total_price_eur,
            status=PaymentStatus.PAID,
        )
        refund = BookingRefund.objects.create(
            payment_attempt=payment,
            amount_eur=Decimal("5.00"),
            status=BookingRefundStatus.REFUNDED,
            reason="Customer-care refund",
            refund_reference="commercial-refund-document",
            provider_refund_id="commercial-provider-refund",
            refunded_at=timezone.now(),
        )

        call_command("retry_toconline_documents", stdout=StringIO())

        issue_credit_note.assert_called_once_with(refund)

    @patch("hotel.management.commands.retry_toconline_documents.issue_booking_document")
    @patch(
        "hotel.management.commands.retry_toconline_documents.issue_confirmed_booking_credit_note"
    )
    def test_duplicate_refund_never_amends_the_original_sale(
        self,
        issue_credit_note,
        issue_document,
    ):
        payment = PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="refunded-duplicate-capture",
            provider_capture_id="refunded-duplicate-capture-id",
            amount_eur=self.booking.total_price_eur,
            status=PaymentStatus.REFUNDED,
            refund_amount_eur=self.booking.total_price_eur,
            refund_reference="refunded-duplicate-reference",
        )
        self.booking.events.create(
            event_type=BookingEventType.DUPLICATE_PAYMENT_RECEIVED,
            details={"payment_attempt_id": payment.pk},
        )

        call_command("retry_toconline_documents", stdout=StringIO())

        issue_document.assert_not_called()
        issue_credit_note.assert_not_called()

    @patch("hotel.management.commands.retry_toconline_documents.issue_booking_document")
    def test_booking_without_a_confirmed_capture_is_not_sent_to_toconline(
        self,
        issue_document,
    ):
        unpaid_booking, _stay = create_booking(
            create_account(username="uncaptured-document-customer"),
            status=BookingStatus.PAID,
            stay_item=create_catalog_item(remote_id="uncaptured-document-stay"),
        )
        unpaid_booking.paid_at = timezone.now()
        unpaid_booking.document_status = BookingDocumentStatus.PENDING
        unpaid_booking.save(update_fields=("paid_at", "document_status", "updated_at"))

        call_command("retry_toconline_documents", stdout=StringIO())

        issue_document.assert_not_called()

    @patch("hotel.management.commands.retry_toconline_documents.issue_booking_document")
    def test_failed_document_for_an_unexpected_capture_is_retried(
        self,
        issue_document,
    ):
        late_booking, _stay = create_booking(
            create_account(username="late-capture-document-customer"),
            status=BookingStatus.PENDING,
            stay_item=create_catalog_item(remote_id="late-capture-document-stay"),
        )
        late_booking.document_status = BookingDocumentStatus.FAILED
        late_booking.save(update_fields=("document_status", "updated_at"))
        PaymentAttempt.objects.create(
            booking=late_booking,
            merchant_reference="late-capture-document",
            provider_capture_id="late-capture-id",
            amount_eur=late_booking.total_price_eur,
            status=PaymentStatus.REFUND_PENDING,
            refund_amount_eur=late_booking.total_price_eur,
            refund_reference="late-capture-refund",
        )

        call_command("retry_toconline_documents", stdout=StringIO())

        issue_document.assert_called_once_with(late_booking)
