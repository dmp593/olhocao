import base64
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import requests
from django.core import mail
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from hotel.models import (
    BookingDocumentStatus,
    BookingStatus,
    PaymentAttempt,
    PaymentEvent,
    PaymentStatus,
)
from hotel.services.payments import (
    EasypayClient,
    PaymentGatewayError,
    PaymentGatewayUnavailable,
    PaymentLinkResult,
    create_payment_attempt,
    process_payment_notification,
    reconcile_pending_refund,
    refresh_payment_attempt,
    request_refund,
    send_booking_payment_link_email,
)
from hotel.services.pricing import set_booking_final_price
from hotel.tests.factories import create_account, create_booking


class FakeLinkClient:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.created = []

    def create_payment_link(
        self,
        payment_attempt,
        *,
        customer,
        expires_at,
        notify_customer=True,
    ):
        if self.fail:
            raise PaymentGatewayError("provider unavailable")
        self.created.append((payment_attempt, customer, expires_at, notify_customer))
        return PaymentLinkResult(
            provider_link_id="link-1",
            url="https://pay.example/link-1",
            qr_code_url="https://pay.example/link-1/qr",
            expires_at=expires_at,
        )


def verified_payment(payment_attempt):
    return {
        "id": "single-1",
        "key": payment_attempt.merchant_reference,
        "value": float(payment_attempt.amount_eur),
        "currency": "EUR",
        "payment_status": "paid",
        "method": {"type": "MBW"},
        "captures": [{"id": "capture-1", "status": "success"}],
        "transactions": [{"id": "transaction-1"}],
    }


class FakeNotificationClient:
    def __init__(self, payment_attempt, *, payment=None, link_status="ACTIVE"):
        self.payment_attempt = payment_attempt
        self.payment = payment or verified_payment(payment_attempt)
        self.link_status = link_status
        self.refund = None
        self.refund_requests = []
        self.chargebacks = []
        self.cancelled_link_ids = []

    def get_payment(self, payment_id):
        return self.payment

    def find_payment_by_key(self, merchant_reference):
        if self.payment is None:
            return None
        return self.payment

    def get_payment_link(self, link_id):
        return {"status": self.link_status}

    def get_refund(self, refund_id):
        return self.refund

    def create_refund(self, capture_id, *, amount, reference):
        self.refund_requests.append((capture_id, amount, reference))
        return {"id": "automatic-refund-1"}

    def list_chargebacks(self, capture_id):
        return self.chargebacks

    def cancel_payment_link(self, link_id):
        self.cancelled_link_ids.append(link_id)


class PaymentAttemptCreationTests(TestCase):
    def setUp(self):
        self.account = create_account()
        self.booking, _ = create_booking(self.account, duration=2)

    def test_creates_hosted_link_for_immutable_booking_total(self):
        client = FakeLinkClient()

        payment = create_payment_attempt(self.booking, client=client)

        self.assertEqual(payment.amount_eur, Decimal("40.00"))
        self.assertEqual(payment.provider_link_id, "link-1")
        self.assertEqual(payment.payment_url, "https://pay.example/link-1")
        self.assertEqual(client.created[0][1]["phone"], "+351912345678")
        self.assertEqual(client.created[0][1]["email"], "customer@example.com")
        self.assertTrue(client.created[0][3])

    def test_staff_delivery_can_disable_provider_email(self):
        client = FakeLinkClient()

        create_payment_attempt(
            self.booking,
            client=client,
            notify_customer=False,
        )

        self.assertFalse(client.created[0][3])

    def test_staff_delivery_emails_the_hosted_link_to_the_customer(self):
        payment = create_payment_attempt(
            self.booking,
            client=FakeLinkClient(),
            notify_customer=False,
        )

        send_booking_payment_link_email(payment)

        self.assertEqual(mail.outbox[0].to, ["customer@example.com"])
        self.assertIn(payment.payment_url, mail.outbox[0].body)

    def test_staff_delivery_requires_a_customer_email_address(self):
        payment = create_payment_attempt(
            self.booking,
            client=FakeLinkClient(),
            notify_customer=False,
        )
        self.account.user.email = ""
        self.account.user.save(update_fields=("email",))

        with self.assertRaisesMessage(PaymentGatewayError, "email address is required"):
            send_booking_payment_link_email(payment)

    def test_reuses_unexpired_pending_link(self):
        existing = PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="existing",
            payment_url="https://pay.example/existing",
            amount_eur=self.booking.total_price_eur,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        client = FakeLinkClient()

        result = create_payment_attempt(self.booking, client=client)

        self.assertEqual(result, existing)
        self.assertEqual(client.created, [])

    def test_expires_old_attempt_before_creating_replacement(self):
        old = PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="old",
            payment_url="https://pay.example/old",
            amount_eur=self.booking.total_price_eur,
            expires_at=timezone.now() - timedelta(minutes=1),
        )

        new = create_payment_attempt(self.booking, client=FakeLinkClient())

        old.refresh_from_db()
        self.assertEqual(old.status, PaymentStatus.EXPIRED)
        self.assertNotEqual(new.pk, old.pk)

    def test_provider_failure_is_persisted_without_losing_booking(self):
        with self.assertRaises(PaymentGatewayError):
            create_payment_attempt(self.booking, client=FakeLinkClient(fail=True))

        payment = self.booking.payment_attempts.get()
        self.assertEqual(payment.status, PaymentStatus.FAILED)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.PENDING)

    def test_rejects_new_payment_link_after_the_booking_start_date(self):
        self.booking.stays.update(
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + timedelta(days=1),
        )

        with self.assertRaisesMessage(PaymentGatewayError, "start date passed"):
            create_payment_attempt(self.booking, client=FakeLinkClient())

        self.assertFalse(self.booking.payment_attempts.exists())

    def test_retry_after_ambiguous_provider_failure_reuses_the_same_reference(self):
        client = FakeLinkClient(fail=True)
        with self.assertRaises(PaymentGatewayError):
            create_payment_attempt(self.booking, client=client)
        failed_attempt = self.booking.payment_attempts.get()

        client.fail = False
        retried_attempt = create_payment_attempt(self.booking, client=client)

        self.assertEqual(retried_attempt.pk, failed_attempt.pk)
        self.assertEqual(
            retried_attempt.merchant_reference,
            failed_attempt.merchant_reference,
        )
        self.assertEqual(self.booking.payment_attempts.count(), 1)
        self.assertEqual(retried_attempt.status, PaymentStatus.PENDING)

    def test_database_rejects_two_pending_attempts(self):
        PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="one",
            amount_eur=self.booking.total_price_eur,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentAttempt.objects.create(
                booking=self.booking,
                merchant_reference="two",
                amount_eur=self.booking.total_price_eur,
            )


class PaymentNotificationTests(TestCase):
    def setUp(self):
        account = create_account()
        self.booking, _ = create_booking(account, duration=2)
        self.payment = PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="booking-reference",
            provider_link_id="link-1",
            payment_url="https://pay.example/link-1",
            amount_eur=self.booking.total_price_eur,
        )
        self.payload = {
            "id": "single-1",
            "key": self.payment.merchant_reference,
            "value": "40.00",
            "currency": "EUR",
            "method": "MBW",
            "transaction": {"id": "transaction-1", "type": "capture"},
        }

    @patch("hotel.services.payments._send_payment_confirmation")
    @patch("hotel.services.payments._issue_document_without_reverting_payment")
    def test_verified_notification_marks_payment_and_booking_paid(
        self,
        issue_document,
        send_confirmation,
    ):
        process_payment_notification(
            self.payload,
            client=FakeNotificationClient(self.payment),
        )

        self.payment.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentStatus.PAID)
        self.assertEqual(self.payment.provider_payment_id, "single-1")
        self.assertEqual(self.payment.provider_capture_id, "capture-1")
        self.assertEqual(self.payment.provider_transaction_id, "transaction-1")
        self.assertEqual(self.payment.payment_method, "MBW")
        self.assertEqual(self.booking.status, BookingStatus.PAID)
        self.assertEqual(self.booking.document_status, BookingDocumentStatus.PENDING)
        self.assertEqual(PaymentEvent.objects.count(), 1)
        issue_document.assert_called_once()
        send_confirmation.assert_called_once()

    @patch("hotel.services.payments._send_payment_confirmation")
    @patch("hotel.services.payments._issue_document_without_reverting_payment")
    def test_duplicate_notification_is_idempotent_and_retries_side_effects(
        self,
        issue_document,
        send_confirmation,
    ):
        client = FakeNotificationClient(self.payment)
        process_payment_notification(self.payload, client=client)
        process_payment_notification(self.payload, client=client)

        self.assertEqual(PaymentEvent.objects.count(), 1)
        self.assertEqual(issue_document.call_count, 2)
        self.assertEqual(send_confirmation.call_count, 2)

    def test_rejects_amount_mismatch(self):
        payment = verified_payment(self.payment)
        payment["value"] = 39

        with self.assertRaises(PaymentGatewayError):
            process_payment_notification(
                self.payload,
                client=FakeNotificationClient(self.payment, payment=payment),
            )

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentStatus.PENDING)
        self.assertEqual(PaymentEvent.objects.count(), 0)

    def test_rejects_paid_response_without_successful_capture(self):
        payment = verified_payment(self.payment)
        payment["captures"] = []

        with self.assertRaises(PaymentGatewayError):
            process_payment_notification(
                self.payload,
                client=FakeNotificationClient(self.payment, payment=payment),
            )

    def test_verified_failed_notification_is_acknowledged_without_confirming_booking(self):
        failed_payment = verified_payment(self.payment)
        failed_payment["payment_status"] = "failed"
        failed_payment["captures"] = []

        result = process_payment_notification(
            {**self.payload, "status": "failed"},
            client=FakeNotificationClient(self.payment, payment=failed_payment),
        )

        result.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertEqual(result.status, PaymentStatus.FAILED)
        self.assertEqual(self.booking.status, BookingStatus.PENDING)
        self.assertIn("safely try again", result.last_error)
        self.assertEqual(PaymentEvent.objects.filter(provider_status="failed").count(), 1)

    def test_verified_chargeback_blocks_booking_and_preserves_financial_history(self):
        self.payment.status = PaymentStatus.PAID
        self.payment.provider_payment_id = "single-1"
        self.payment.provider_capture_id = "capture-1"
        self.payment.save()
        self.booking.status = BookingStatus.PAID
        self.booking.save(update_fields=("status", "updated_at"))
        client = FakeNotificationClient(self.payment)
        client.chargebacks = [
            {
                "id": "chargeback-1",
                "amount": "40.00",
                "capture": {"id": "capture-1", "payment_id": "single-1"},
            }
        ]
        payload = {
            "id": "single-1",
            "key": self.payment.merchant_reference,
            "type": "chargeback",
            "status": "success",
        }

        process_payment_notification(payload, client=client)
        process_payment_notification(payload, client=client)

        self.payment.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentStatus.DISPUTED)
        self.assertEqual(self.booking.status, BookingStatus.PAYMENT_DISPUTED)
        self.assertEqual(PaymentEvent.objects.filter(event_type="chargeback").count(), 1)
        self.assertTrue(self.booking.events.filter(event_type="payment_disputed").exists())
        self.assertEqual(self.booking.toconline_amend_document_id, "")

    def test_chargeback_is_rejected_when_provider_capture_does_not_match(self):
        self.payment.status = PaymentStatus.PAID
        self.payment.provider_payment_id = "single-1"
        self.payment.provider_capture_id = "capture-1"
        self.payment.save()
        client = FakeNotificationClient(self.payment)
        client.chargebacks = [
            {
                "id": "chargeback-1",
                "amount": "40.00",
                "capture": {"id": "different-capture", "payment_id": "single-1"},
            }
        ]

        with self.assertRaisesMessage(PaymentGatewayError, "capture does not match"):
            process_payment_notification(
                {
                    "id": "single-1",
                    "key": self.payment.merchant_reference,
                    "type": "chargeback",
                    "status": "success",
                },
                client=client,
            )

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentStatus.PAID)
        self.assertFalse(PaymentEvent.objects.exists())

    @patch("hotel.services.payments._send_payment_confirmation")
    @patch("hotel.services.payments._issue_document_without_reverting_payment")
    def test_reconciliation_recovers_a_missed_webhook(
        self,
        issue_document,
        send_confirmation,
    ):
        result = refresh_payment_attempt(
            self.payment,
            client=FakeNotificationClient(self.payment),
        )

        result.refresh_from_db()
        self.assertEqual(result.status, PaymentStatus.PAID)
        self.assertEqual(result.provider_capture_id, "capture-1")
        self.assertTrue(PaymentEvent.objects.filter(event_type="reconciliation").exists())

    @patch("hotel.services.payments._send_unexpected_payment_notice")
    @patch("hotel.services.payments._issue_document_without_reverting_payment")
    def test_payment_after_cancellation_is_refunded_without_reopening_booking(
        self,
        issue_document,
        send_notice,
    ):
        self.booking.status = BookingStatus.CANCELLED
        self.booking.save(update_fields=("status", "updated_at"))
        client = FakeNotificationClient(self.payment)

        process_payment_notification(self.payload, client=client)

        self.booking.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.CANCELLED)
        self.assertEqual(self.payment.status, PaymentStatus.REFUND_PENDING)
        self.assertEqual(client.refund_requests[0][1], self.payment.amount_eur)
        self.assertTrue(
            self.booking.events.filter(event_type="payment_after_cancellation").exists()
        )
        issue_document.assert_called_once()
        send_notice.assert_called_once()

    @patch("hotel.services.payments._send_unexpected_payment_notice")
    @patch("hotel.services.payments._issue_document_without_reverting_payment")
    def test_second_successful_payment_is_refunded_without_crediting_original_sale(
        self,
        issue_document,
        send_notice,
    ):
        PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="first-successful-payment",
            provider_capture_id="capture-original",
            amount_eur=self.booking.total_price_eur,
            status=PaymentStatus.PAID,
        )
        self.booking.status = BookingStatus.PAID
        self.booking.save(update_fields=("status", "updated_at"))
        client = FakeNotificationClient(self.payment)

        process_payment_notification(self.payload, client=client)

        self.booking.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.PAID)
        self.assertEqual(self.payment.status, PaymentStatus.REFUND_PENDING)
        self.assertTrue(
            self.booking.events.filter(event_type="duplicate_payment_received").exists()
        )
        issue_document.assert_not_called()
        send_notice.assert_called_once()

    @patch("hotel.services.payments._send_unexpected_payment_notice")
    @patch("hotel.services.payments._issue_document_without_reverting_payment")
    def test_second_payment_remains_a_duplicate_after_booking_cancellation(
        self,
        issue_document,
        send_notice,
    ):
        PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="paid-before-cancellation",
            provider_capture_id="capture-before-cancellation",
            amount_eur=self.booking.total_price_eur,
            status=PaymentStatus.PAID,
        )
        self.booking.status = BookingStatus.CANCELLED
        self.booking.save(update_fields=("status", "updated_at"))

        process_payment_notification(
            self.payload,
            client=FakeNotificationClient(self.payment),
        )

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentStatus.REFUND_PENDING)
        self.assertTrue(
            self.booking.events.filter(event_type="duplicate_payment_received").exists()
        )
        self.assertFalse(
            self.booking.events.filter(event_type="payment_after_cancellation").exists()
        )
        issue_document.assert_not_called()
        send_notice.assert_called_once()

    @patch("hotel.services.payments._send_unexpected_payment_notice")
    @patch("hotel.services.payments._issue_document_without_reverting_payment")
    def test_old_link_paid_after_price_change_is_refunded_without_confirming_booking(
        self,
        issue_document,
        send_notice,
    ):
        staff = create_account(username="late-price-change-staff", staff=True)
        client = FakeNotificationClient(self.payment)
        set_booking_final_price(
            self.booking,
            final_price_eur=Decimal("20.00"),
            reason="Customer care price correction",
            actor=staff.user,
            payment_client=client,
        )

        process_payment_notification(self.payload, client=client)

        self.booking.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(client.cancelled_link_ids, ["link-1"])
        self.assertEqual(self.booking.status, BookingStatus.PENDING)
        self.assertEqual(self.booking.total_price_eur, Decimal("20.00"))
        self.assertIsNone(self.booking.paid_at)
        self.assertEqual(self.payment.status, PaymentStatus.REFUND_PENDING)
        self.assertEqual(client.refund_requests[0][1], Decimal("40.00"))
        self.assertTrue(
            self.booking.events.filter(event_type="payment_after_price_change").exists()
        )
        issue_document.assert_not_called()
        send_notice.assert_called_once()

    @patch("hotel.services.payments._send_unexpected_payment_notice")
    @patch("hotel.services.payments._issue_document_without_reverting_payment")
    def test_payment_after_service_start_is_refunded_without_confirming_booking(
        self,
        issue_document,
        send_notice,
    ):
        self.booking.stays.update(
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + timedelta(days=1),
        )
        client = FakeNotificationClient(self.payment)

        process_payment_notification(self.payload, client=client)

        self.booking.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.PENDING)
        self.assertEqual(self.payment.status, PaymentStatus.REFUND_PENDING)
        self.assertEqual(client.refund_requests[0][1], Decimal("40.00"))
        self.assertTrue(
            self.booking.events.filter(event_type="payment_after_service_start").exists()
        )
        issue_document.assert_called_once()
        send_notice.assert_called_once()


class RefundTests(TestCase):
    def setUp(self):
        account = create_account()
        self.booking, _ = create_booking(account, status=BookingStatus.PAID)
        self.payment = PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="paid-booking",
            provider_payment_id="single-1",
            provider_capture_id="capture-1",
            amount_eur=self.booking.total_price_eur,
            status=PaymentStatus.PAID,
        )

    def test_refund_uses_capture_id_and_persists_requested_amount(self):
        client = FakeRefundClient()

        request_refund(self.payment, Decimal("30.00"), client=client)

        self.payment.refresh_from_db()
        self.assertEqual(client.capture_id, "capture-1")
        self.assertEqual(self.payment.refund_amount_eur, Decimal("30.00"))
        self.assertEqual(self.payment.provider_refund_id, "refund-1")
        self.assertEqual(self.payment.status, PaymentStatus.REFUND_PENDING)

    @patch("hotel.services.invoicing.issue_booking_credit_note", return_value="credit-note-1")
    def test_refund_notification_is_verified_and_idempotent(self, issue_credit_note):
        client = FakeRefundClient()
        request_refund(self.payment, Decimal("30.00"), client=client)
        self.payment.refresh_from_db()
        notification_client = FakeNotificationClient(self.payment)
        notification_client.refund = {
            "id": "refund-1",
            "status": "success",
            "transaction_key": self.payment.refund_reference,
            "value": 30,
        }
        payload = {
            "id": "refund-1",
            "key": self.payment.refund_reference,
            "type": "refund",
            "status": "success",
            "date": "2026-09-01 12:00:00",
        }

        process_payment_notification(payload, client=notification_client)
        process_payment_notification(payload, client=notification_client)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentStatus.REFUNDED)
        self.assertIsNotNone(self.payment.refunded_at)
        self.assertEqual(PaymentEvent.objects.filter(event_type="refund").count(), 1)
        self.assertEqual(issue_credit_note.call_count, 2)
        expected_percentage = Decimal("30.00") / self.payment.amount_eur
        issue_credit_note.assert_called_with(self.booking, expected_percentage)

    @patch("hotel.services.invoicing.issue_booking_credit_note")
    def test_duplicate_payment_refund_does_not_credit_the_original_sale(
        self,
        issue_credit_note,
    ):
        client = FakeRefundClient()
        request_refund(self.payment, Decimal("30.00"), client=client)
        self.payment.refresh_from_db()
        self.booking.events.create(
            event_type="duplicate_payment_received",
            details={"payment_attempt_id": self.payment.pk},
        )
        notification_client = FakeNotificationClient(self.payment)
        notification_client.refund = {
            "id": "refund-1",
            "status": "success",
            "transaction_key": self.payment.refund_reference,
            "value": 30,
        }

        process_payment_notification(
            {
                "id": "refund-1",
                "key": self.payment.refund_reference,
                "type": "refund",
                "status": "success",
            },
            client=notification_client,
        )

        issue_credit_note.assert_not_called()

    def test_failed_refund_retry_reuses_the_same_provider_reference(self):
        client = FakeRefundClient(fail=True)
        with self.assertRaises(PaymentGatewayError):
            request_refund(self.payment, Decimal("30.00"), client=client)
        self.payment.refresh_from_db()
        failed_reference = self.payment.refund_reference

        client.fail = False
        request_refund(self.payment, Decimal("30.00"), client=client)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.refund_reference, failed_reference)
        self.assertEqual(client.references, [failed_reference, failed_reference])

    def test_pending_refund_without_provider_id_is_resubmitted_idempotently(self):
        self.payment.status = PaymentStatus.REFUND_PENDING
        self.payment.refund_amount_eur = Decimal("30.00")
        self.payment.refund_reference = "refund-recovery-reference"
        self.payment.provider_refund_id = ""
        self.payment.save(
            update_fields=(
                "status",
                "refund_amount_eur",
                "refund_reference",
                "provider_refund_id",
                "updated_at",
            )
        )
        client = FakeRefundClient()

        result = request_refund(self.payment, Decimal("30.00"), client=client)

        self.assertEqual(client.references, ["refund-recovery-reference"])
        self.assertEqual(result.provider_refund_id, "refund-1")
        self.assertEqual(result.status, PaymentStatus.REFUND_PENDING)

    def test_failed_refund_retry_rejects_a_different_amount(self):
        client = FakeRefundClient(fail=True)
        with self.assertRaises(PaymentGatewayError):
            request_refund(self.payment, Decimal("30.00"), client=client)
        self.payment.refresh_from_db()

        with self.assertRaisesMessage(PaymentGatewayError, "originally requested amount"):
            request_refund(self.payment, Decimal("20.00"), client=client)

        self.assertEqual(client.references, [self.payment.refund_reference])

    @patch("hotel.services.invoicing.issue_booking_credit_note", return_value="credit-note-1")
    def test_pending_refund_is_reconciled_when_the_webhook_is_missing(
        self,
        issue_credit_note,
    ):
        client = FakeRefundClient()
        request_refund(self.payment, Decimal("30.00"), client=client)
        self.payment.refresh_from_db()
        client.refund = {
            "id": "refund-1",
            "status": "success",
            "transaction_key": self.payment.refund_reference,
            "value": "30.00",
        }

        result = reconcile_pending_refund(self.payment, client=client)

        self.assertEqual(result.status, PaymentStatus.REFUNDED)
        issue_credit_note.assert_called_once()

    @patch("hotel.services.invoicing.issue_booking_credit_note")
    def test_failed_refund_notification_is_acknowledged_and_queued_for_retry(
        self,
        issue_credit_note,
    ):
        client = FakeRefundClient()
        request_refund(self.payment, Decimal("30.00"), client=client)
        self.payment.refresh_from_db()
        notification_client = FakeNotificationClient(self.payment)
        notification_client.refund = {
            "id": "refund-1",
            "status": "failed",
            "transaction_key": self.payment.refund_reference,
            "value": "30.00",
        }

        result = process_payment_notification(
            {
                "id": "refund-1",
                "key": self.payment.refund_reference,
                "type": "refund",
                "status": "failed",
            },
            client=notification_client,
        )

        result.refresh_from_db()
        self.assertEqual(result.status, PaymentStatus.REFUND_FAILED)
        self.assertEqual(PaymentEvent.objects.filter(event_type="refund").count(), 1)
        issue_credit_note.assert_not_called()


class FakeRefundClient:
    def __init__(self, *, fail=False):
        self.capture_id = None
        self.fail = fail
        self.references = []
        self.refund = None

    def create_refund(self, capture_id, *, amount, reference):
        self.capture_id = capture_id
        self.references.append(reference)
        if self.fail:
            raise PaymentGatewayError("provider unavailable")
        return {"id": "refund-1"}

    def get_refund(self, refund_id):
        return self.refund


@override_settings(
    EASYPAY_WEBHOOK_USERNAME="webhook-user",
    EASYPAY_WEBHOOK_PASSWORD="a-secure-webhook-password-value",
)
class EasypayWebhookViewTests(TestCase):
    def authorization_header(self, username="webhook-user", password=None):
        password = password or "a-secure-webhook-password-value"
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        return f"Basic {token}"

    def test_rejects_missing_or_invalid_basic_auth(self):
        url = reverse("hotel:easypay_webhook")
        response = self.client.post(
            url,
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        response = self.client.post(
            url,
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=self.authorization_header(password="wrong"),
        )
        self.assertEqual(response.status_code, 401)

    @patch("hotel.booking_views.process_payment_notification")
    def test_accepts_authenticated_json_notification(self, process_notification):
        payload = {"id": "event", "key": "booking"}
        response = self.client.post(
            reverse("hotel:easypay_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=self.authorization_header(),
        )

        self.assertEqual(response.status_code, 200)
        process_notification.assert_called_once_with(payload)

    @patch("hotel.booking_views.process_payment_notification")
    def test_rejects_oversized_payload_before_processing(self, process_notification):
        response = self.client.post(
            reverse("hotel:easypay_webhook"),
            data="x" * (64 * 1024 + 1),
            content_type="application/json",
            HTTP_AUTHORIZATION=self.authorization_header(),
        )

        self.assertEqual(response.status_code, 413)
        process_notification.assert_not_called()

    @patch("hotel.booking_views.process_payment_notification")
    def test_rejects_json_that_is_not_an_object(self, process_notification):
        response = self.client.post(
            reverse("hotel:easypay_webhook"),
            data="[]",
            content_type="application/json",
            HTTP_AUTHORIZATION=self.authorization_header(),
        )

        self.assertEqual(response.status_code, 400)
        process_notification.assert_not_called()

    @patch("hotel.booking_views.process_payment_notification")
    def test_returns_retryable_status_when_provider_verification_is_unavailable(
        self,
        process_notification,
    ):
        process_notification.side_effect = PaymentGatewayUnavailable("provider unavailable")

        response = self.client.post(
            reverse("hotel:easypay_webhook"),
            data=json.dumps({"id": "event", "key": "booking"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=self.authorization_header(),
        )

        self.assertEqual(response.status_code, 503)


class FakeResponse:
    def __init__(self, data, status_code=201):
        self.data = data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        return self.data


class RecordingSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


@override_settings(
    EASYPAY_ENVIRONMENT="sandbox",
    EASYPAY_ACCOUNT_ID="account-id",
    EASYPAY_API_KEY="api-key",
    EASYPAY_PAYMENT_METHODS=["CC", "MBW", "MB"],
)
class EasypayClientContractTests(TestCase):
    def test_pay_by_link_payload_matches_official_contract(self):
        account = create_account()
        booking, _ = create_booking(account)
        payment = PaymentAttempt.objects.create(
            booking=booking,
            merchant_reference="contract-test",
            amount_eur=booking.total_price_eur,
        )
        session = RecordingSession(
            FakeResponse(
                {
                    "id": "link-1",
                    "url": "https://pay.example/link-1",
                    "image": "https://pay.example/link-1/qr",
                }
            )
        )
        expires_at = timezone.now() + timedelta(hours=24)

        EasypayClient(session=session).create_payment_link(
            payment,
            customer={
                "name": "Test Customer",
                "email": "customer@example.com",
                "phone": "+351912345678",
                "language": "PT",
            },
            expires_at=expires_at,
        )

        method, url, request = session.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://api.test.easypay.pt/2.0/link")
        self.assertEqual(request["json"]["type"], "SINGLE")
        self.assertEqual(
            request["json"]["payment"]["single"]["requested_amount"],
            "60.00",
        )
        self.assertEqual(
            request["json"]["payment"]["capture"]["key"],
            "contract-test",
        )
        self.assertEqual(request["json"]["communication_channels"], ["EMAIL"])
        self.assertEqual(request["headers"]["Idempotency-Key"], "contract-test")

    def test_disabling_an_already_missing_payment_link_is_idempotent(self):
        session = RecordingSession(FakeResponse({}, status_code=404))

        EasypayClient(session=session).cancel_payment_link("already-disabled-link")

        method, url, _request = session.calls[0]
        self.assertEqual(method, "DELETE")
        self.assertEqual(
            url,
            "https://api.test.easypay.pt/2.0/link/already-disabled-link",
        )
