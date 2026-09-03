from decimal import Decimal
from unittest.mock import patch

from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from activities.models import (
    CreditLedgerEntry,
    EntitlementRefund,
    EntitlementStatus,
    LedgerReason,
    OfferingMode,
    RefundStatus,
    ServiceFulfilment,
    ServiceFulfilmentStatus,
)
from activities.services.billing import process_activity_notification
from activities.services.refunds import (
    confirm_entitlement_refund,
    request_entitlement_refund,
    retry_entitlement_refund_request,
)
from activities.tests.factories import create_offering
from catalog.models import CatalogWorkflow
from hotel.models import BookingDocumentStatus
from hotel.services.payments import PaymentGatewayError
from hotel.tests.factories import create_account, create_pet


class FakeRefundClient:
    def __init__(self):
        self.requests = []
        self.refund = None
        self.fail_requests = False

    def create_refund(self, provider_capture_id, *, amount, reference):
        self.requests.append((provider_capture_id, amount, reference))
        if self.fail_requests:
            raise PaymentGatewayError("provider unavailable")
        return {"id": "provider-refund-1"}

    def get_refund(self, provider_refund_id):
        return self.refund


class EntitlementRefundTests(TestCase):
    def setUp(self):
        self.staff = create_account(username="refund-manager", staff=True).user
        self.customer = create_account(username="refund-customer")
        self.pet = create_pet(self.customer)
        self.offering = create_offering(
            mode=OfferingMode.PACK,
            workflow=CatalogWorkflow.FLEXIBLE,
            remote_id="refundable-flexible-pack",
        )
        self.client = FakeRefundClient()

    def create_entitlement(self, **overrides):
        values = {
            "customer": self.customer,
            "pet": self.pet,
            "status": EntitlementStatus.ACTIVE,
            "item_name_snapshot": self.offering.catalog_item.name,
            "unit_price_eur_snapshot": self.offering.price_eur,
            "merchant_reference": "refundable-purchase",
            "provider_payment_id": "payment-1",
            "provider_capture_id": "capture-1",
            "paid_at": timezone.now(),
            "toconline_sale_document_id": "original-receipt-1",
            "document_status": BookingDocumentStatus.ISSUED,
        }
        values.update(overrides)
        return self.offering.entitlements.create(**values)

    def request_refund(self, entitlement, *, amount="10.00", revoke=False):
        return request_entitlement_refund(
            entitlement,
            amount=Decimal(amount),
            actor=self.staff,
            reason="Customer-care refund",
            revoke_remaining_access=revoke,
            client=self.client,
        )

    def provider_confirms(self, refund, *, amount=None):
        self.client.refund = {
            "id": refund.provider_refund_id,
            "status": "success",
            "transaction_key": refund.refund_reference,
            "value": amount or str(refund.amount_eur),
        }

    def test_uncaptured_purchase_cannot_be_refunded(self):
        entitlement = self.create_entitlement(
            merchant_reference="uncaptured-purchase",
            provider_capture_id="",
            paid_at=None,
            toconline_sale_document_id="",
        )

        with self.assertRaisesMessage(PaymentGatewayError, "no confirmed payment capture"):
            self.request_refund(entitlement)

        self.assertFalse(EntitlementRefund.objects.exists())

    def test_pending_and_confirmed_partial_refunds_reserve_the_exact_balance(self):
        entitlement = self.create_entitlement()

        first = self.request_refund(entitlement, amount="20.00")
        second = self.request_refund(entitlement, amount="29.90")

        self.assertEqual(first.status, RefundStatus.PENDING)
        self.assertEqual(second.status, RefundStatus.PENDING)
        with self.assertRaisesMessage(PaymentGatewayError, "exceeds the refundable balance"):
            self.request_refund(entitlement, amount="0.01")

    def test_failed_provider_request_can_retry_with_the_same_reference(self):
        entitlement = self.create_entitlement()
        self.client.fail_requests = True

        with self.assertRaises(PaymentGatewayError):
            self.request_refund(entitlement, amount="12.50")

        refund = EntitlementRefund.objects.get()
        original_reference = refund.refund_reference
        self.assertEqual(refund.status, RefundStatus.FAILED)

        self.client.fail_requests = False
        retried = retry_entitlement_refund_request(
            refund,
            actor=self.staff,
            client=self.client,
        )

        self.assertEqual(retried.status, RefundStatus.PENDING)
        self.assertEqual(retried.refund_reference, original_reference)
        self.assertEqual(
            [request[2] for request in self.client.requests],
            [original_reference, original_reference],
        )

    def test_pending_refund_without_provider_id_is_resubmitted_idempotently(self):
        entitlement = self.create_entitlement()
        refund = EntitlementRefund.objects.create(
            entitlement=entitlement,
            amount_eur=Decimal("12.50"),
            status=RefundStatus.PENDING,
            reason="Recover interrupted request",
            refund_reference="purchase-refund-recovery-reference",
            requested_by=self.staff,
        )

        result = retry_entitlement_refund_request(
            refund,
            actor=self.staff,
            client=self.client,
        )

        self.assertEqual(
            self.client.requests,
            [
                (
                    "capture-1",
                    Decimal("12.50"),
                    "purchase-refund-recovery-reference",
                )
            ],
        )
        self.assertEqual(result.provider_refund_id, "provider-refund-1")

    def test_confirmation_rejects_a_mismatched_provider_identity(self):
        refund = self.request_refund(self.create_entitlement())
        self.client.refund = {
            "status": "success",
            "transaction_key": "another-refund",
            "value": "10.00",
        }

        with self.assertRaisesMessage(PaymentGatewayError, "reference does not match"):
            confirm_entitlement_refund(
                refund,
                provider_refund_id=refund.provider_refund_id,
                client=self.client,
            )

        refund.refresh_from_db()
        self.assertEqual(refund.status, RefundStatus.PENDING)

    def test_confirmed_refund_can_revoke_unused_access_without_rewriting_sale(self):
        entitlement = self.create_entitlement()
        CreditLedgerEntry.objects.create(
            entitlement=entitlement,
            delta=5,
            reason=LedgerReason.PURCHASE,
            idempotency_key="refundable-pack-purchase",
        )
        fulfilment = ServiceFulfilment.objects.create(
            entitlement=entitlement,
            pet=self.pet,
            status=ServiceFulfilmentStatus.REQUESTED,
            consumes_entitlement_use=True,
        )
        refund = self.request_refund(entitlement, amount="20.00", revoke=True)
        self.provider_confirms(refund)

        with patch(
            "activities.services.refunds.issue_entitlement_refund_credit_note",
            return_value="credit-note-1",
        ) as issue_credit_note:
            confirmed = confirm_entitlement_refund(
                refund,
                provider_refund_id=refund.provider_refund_id,
                client=self.client,
            )

        confirmed.refresh_from_db()
        entitlement.refresh_from_db()
        fulfilment.refresh_from_db()
        self.assertEqual(confirmed.status, RefundStatus.REFUNDED)
        self.assertEqual(entitlement.status, EntitlementStatus.CANCELLED)
        self.assertFalse(entitlement.has_access)
        self.assertEqual(entitlement.credit_balance, 0)
        self.assertEqual(fulfilment.status, ServiceFulfilmentStatus.CANCELLED)
        self.assertEqual(entitlement.toconline_sale_document_id, "original-receipt-1")
        issue_credit_note.assert_called_once_with(confirmed)

    def test_toconline_failure_does_not_undo_a_confirmed_provider_refund(self):
        entitlement = self.create_entitlement()
        refund = self.request_refund(entitlement)
        self.provider_confirms(refund)

        with patch(
            "activities.services.refunds.issue_entitlement_refund_credit_note",
            side_effect=RuntimeError("TOConline offline"),
        ):
            confirmed = confirm_entitlement_refund(
                refund,
                provider_refund_id=refund.provider_refund_id,
                client=self.client,
            )

        confirmed.refresh_from_db()
        entitlement.refresh_from_db()
        self.assertEqual(confirmed.status, RefundStatus.REFUNDED)
        self.assertIn("TOConline offline", confirmed.document_error)
        self.assertEqual(entitlement.toconline_sale_document_id, "original-receipt-1")

    def test_refund_webhook_is_idempotent(self):
        refund = self.request_refund(self.create_entitlement())
        self.provider_confirms(refund)
        payload = {
            "id": refund.provider_refund_id,
            "key": refund.refund_reference,
            "type": "refund",
            "status": "success",
        }

        with patch(
            "activities.services.refunds.issue_entitlement_refund_credit_note",
            return_value="credit-note-1",
        ) as issue_credit_note:
            process_activity_notification(payload, client=self.client)
            process_activity_notification(payload, client=self.client)

        refund.refresh_from_db()
        self.assertEqual(refund.status, RefundStatus.REFUNDED)
        issue_credit_note.assert_called_once()

    def test_refund_history_cannot_be_deleted(self):
        refund = self.request_refund(self.create_entitlement())

        with self.assertRaises(ProtectedError):
            refund.delete()
        with self.assertRaises(ProtectedError):
            EntitlementRefund.objects.filter(pk=refund.pk).delete()
