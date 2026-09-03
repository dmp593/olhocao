from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from activities.models import (
    EntitlementStatus,
    OfferingMode,
    RefundStatus,
    ServiceEntitlement,
    SubscriptionCycle,
    SubscriptionCycleStatus,
    SubscriptionManagementAction,
)
from activities.services.billing import create_entitlement
from activities.services.subscriptions import (
    cancel_subscription_at_period_end,
    confirm_subscription_cycle_refund,
    reconcile_subscription_cycle_refund,
    request_subscription_cycle_refund,
    retry_subscription_documents,
    schedule_subscription_resume,
    set_subscription_access_block,
    suspend_subscription,
    terminate_subscription,
)
from activities.tests.factories import create_catalog_type, create_offering
from catalog.models import CatalogWorkflow
from hotel.models import BookingDocumentStatus
from hotel.services.payments import PaymentGatewayError, SubscriptionResult
from hotel.tests.factories import create_account, create_pet


class FakeSubscriptionClient:
    def __init__(self):
        self.stopped = []
        self.resumed = []
        self.refund_requests = []
        self.refund = None
        self.fail_refund = False

    def create_subscription(self, **kwargs):
        return SubscriptionResult(
            provider_subscription_id=f"subscription-{kwargs['merchant_reference']}",
            authorisation_url="https://pay.example/authorise",
        )

    def stop_subscription(self, provider_subscription_id):
        self.stopped.append(provider_subscription_id)
        return {"status": "inactive"}

    def resume_subscription(self, provider_subscription_id, *, frequency, start_at):
        self.resumed.append((provider_subscription_id, frequency, start_at))
        return {"status": "active"}

    def create_refund(self, provider_capture_id, *, amount, reference):
        self.refund_requests.append((provider_capture_id, amount, reference))
        if self.fail_refund:
            raise PaymentGatewayError("provider unavailable")
        return {"id": "refund-1"}

    def get_refund(self, provider_refund_id):
        return self.refund


class SubscriptionExclusivityTests(TestCase):
    def setUp(self):
        self.customer = create_account()
        self.pet = create_pet(self.customer)
        self.client = FakeSubscriptionClient()

    def test_dog_cannot_hold_two_open_daycare_subscriptions(self):
        first_offering = create_offering(
            mode=OfferingMode.SUBSCRIPTION,
            remote_id="daycare-subscription-1",
        )
        second_offering = create_offering(
            mode=OfferingMode.SUBSCRIPTION,
            remote_id="daycare-subscription-2",
        )
        create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=first_offering,
            actor=self.customer.user,
            client=self.client,
        )

        with self.assertRaisesMessage(PaymentGatewayError, "already has an open subscription"):
            create_entitlement(
                customer=self.customer,
                pet=self.pet,
                offering=second_offering,
                actor=self.customer.user,
                client=self.client,
            )

        self.assertEqual(ServiceEntitlement.objects.count(), 1)

    def test_database_constraint_prevents_concurrent_duplicate_subscription(self):
        first_offering = create_offering(
            mode=OfferingMode.SUBSCRIPTION,
            remote_id="concurrent-daycare-1",
        )
        second_offering = create_offering(
            mode=OfferingMode.SUBSCRIPTION,
            remote_id="concurrent-daycare-2",
        )
        first_offering.entitlements.create(
            customer=self.customer,
            pet=self.pet,
            status=EntitlementStatus.ACTIVE,
            item_name_snapshot=first_offering.catalog_item.name,
            unit_price_eur_snapshot=first_offering.price_eur,
            merchant_reference="first-concurrent-subscription",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            second_offering.entitlements.create(
                customer=self.customer,
                pet=self.pet,
                status=EntitlementStatus.PENDING,
                item_name_snapshot=second_offering.catalog_item.name,
                unit_price_eur_snapshot=second_offering.price_eur,
                merchant_reference="second-concurrent-subscription",
            )

    def test_daycare_and_training_subscriptions_can_coexist(self):
        daycare = create_offering(
            mode=OfferingMode.SUBSCRIPTION,
            remote_id="daycare-subscription",
        )
        training = create_offering(
            mode=OfferingMode.SUBSCRIPTION,
            catalog_type=create_catalog_type(
                workflow=CatalogWorkflow.TIMED_SLOT,
                slug="training-subscription-type",
                name="Training",
            ),
            remote_id="training-subscription",
        )

        create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=daycare,
            actor=self.customer.user,
            client=self.client,
        )
        create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=training,
            actor=self.customer.user,
            client=self.client,
        )

        self.assertEqual(ServiceEntitlement.objects.count(), 2)

    def test_contract_terms_do_not_change_with_later_offering_edits(self):
        offering = create_offering(
            mode=OfferingMode.SUBSCRIPTION,
            remote_id="snapshot-subscription",
        )
        entitlement = offering.entitlements.create(
            customer=self.customer,
            pet=self.pet,
            status=EntitlementStatus.ACTIVE,
            item_name_snapshot=offering.catalog_item.name,
            unit_price_eur_snapshot=offering.price_eur,
            merchant_reference="snapshot-contract",
        )

        offering.sessions_per_week = 5
        offering.billing_frequency = "1W"
        offering.save(update_fields=("sessions_per_week", "billing_frequency"))
        entitlement.refresh_from_db()

        self.assertEqual(entitlement.sessions_per_week_snapshot, 2)
        self.assertEqual(entitlement.billing_frequency_snapshot, "1M")


class SubscriptionManagementTests(TestCase):
    def setUp(self):
        self.staff = create_account(username="subscription-manager", staff=True).user
        self.customer = create_account(username="subscriber")
        self.pet = create_pet(self.customer)
        self.offering = create_offering(
            mode=OfferingMode.SUBSCRIPTION,
            remote_id="managed-subscription",
        )
        self.entitlement = self.offering.entitlements.create(
            customer=self.customer,
            pet=self.pet,
            status=EntitlementStatus.ACTIVE,
            item_name_snapshot=self.offering.catalog_item.name,
            unit_price_eur_snapshot=self.offering.price_eur,
            merchant_reference="managed-contract",
            provider_subscription_id="provider-subscription-1",
            current_period_start=timezone.now(),
            current_period_end=timezone.now() + timedelta(days=20),
        )
        self.client = FakeSubscriptionClient()

    def test_suspend_and_scheduled_resume_are_audited(self):
        suspended = suspend_subscription(
            self.entitlement,
            actor=self.staff,
            reason="Temporary medical hold",
            client=self.client,
        )
        resume_at = timezone.now() + timedelta(days=10)
        resumed = schedule_subscription_resume(
            suspended,
            actor=self.staff,
            reason="Customer requested restart",
            resume_at=resume_at,
            client=self.client,
        )

        self.assertEqual(resumed.status, EntitlementStatus.SUSPENDED)
        self.assertEqual(resumed.resume_scheduled_at, resume_at)
        self.assertEqual(self.client.stopped, ["provider-subscription-1"])
        self.assertEqual(self.client.resumed[0][1], "1M")
        self.assertEqual(
            list(resumed.management_events.values_list("action", flat=True)),
            [
                SubscriptionManagementAction.RESUME_SCHEDULED,
                SubscriptionManagementAction.SUSPENDED,
            ],
        )

    def test_cancelling_suspended_subscription_does_not_restore_access(self):
        suspended = suspend_subscription(
            self.entitlement,
            actor=self.staff,
            reason="Operational safety hold",
            client=self.client,
        )

        cancelled = cancel_subscription_at_period_end(
            suspended,
            actor=self.staff,
            reason="Customer ended the plan",
            client=self.client,
        )

        self.assertEqual(cancelled.status, EntitlementStatus.CANCEL_AT_PERIOD_END)
        self.assertIsNotNone(cancelled.access_blocked_at)
        self.assertFalse(cancelled.has_access)

    def test_pending_subscription_can_be_terminated_to_release_exclusivity(self):
        self.entitlement.status = EntitlementStatus.PENDING
        self.entitlement.save(update_fields=("status", "updated_at"))

        terminated = terminate_subscription(
            self.entitlement,
            actor=self.staff,
            reason="Authorisation was abandoned",
            client=self.client,
        )

        self.assertEqual(terminated.status, EntitlementStatus.CANCELLED)
        self.assertEqual(
            terminated.management_events.get().action,
            SubscriptionManagementAction.TERMINATED,
        )

    def test_operational_access_block_does_not_change_billing_status(self):
        blocked = set_subscription_access_block(
            self.entitlement,
            actor=self.staff,
            reason="Vaccination document missing",
            blocked=True,
        )

        self.assertEqual(blocked.status, EntitlementStatus.ACTIVE)
        self.assertFalse(blocked.has_access)
        self.assertEqual(blocked.access_blocked_reason, "Vaccination document missing")

    def test_financial_records_cannot_be_deleted(self):
        cycle = SubscriptionCycle.objects.create(
            entitlement=self.entitlement,
            provider_cycle_id="immutable-cycle",
            status=SubscriptionCycleStatus.PAID,
            amount_eur=Decimal("49.90"),
        )

        with self.assertRaises(ProtectedError):
            cycle.delete()
        with self.assertRaises(ProtectedError):
            ServiceEntitlement.objects.filter(pk=self.entitlement.pk).delete()

    def test_refund_keeps_original_receipt_and_issues_credit_note(self):
        cycle = SubscriptionCycle.objects.create(
            entitlement=self.entitlement,
            provider_cycle_id="refundable-cycle",
            provider_capture_id="capture-1",
            status=SubscriptionCycleStatus.PAID,
            amount_eur=Decimal("49.90"),
            toconline_sale_document_id="receipt-1",
            document_status=BookingDocumentStatus.ISSUED,
        )
        requested = request_subscription_cycle_refund(
            cycle,
            amount=Decimal("20.00"),
            actor=self.staff,
            reason="Partial goodwill refund",
            client=self.client,
        )
        self.client.refund = {
            "id": "refund-1",
            "status": "success",
            "transaction_key": requested.refund_reference,
            "value": "20.00",
        }

        with patch(
            "activities.services.subscriptions.issue_activity_credit_note",
            return_value="credit-note-1",
        ) as issue_credit_note:
            confirmed = confirm_subscription_cycle_refund(
                requested,
                provider_refund_id="refund-1",
                client=self.client,
            )

        confirmed.refresh_from_db()
        self.assertEqual(confirmed.refund_status, RefundStatus.REFUNDED)
        self.assertEqual(confirmed.toconline_sale_document_id, "receipt-1")
        issue_credit_note.assert_called_once()

    def test_refund_requires_provider_capture_identifier(self):
        cycle = SubscriptionCycle.objects.create(
            entitlement=self.entitlement,
            provider_cycle_id="cycle-without-capture",
            status=SubscriptionCycleStatus.PAID,
            amount_eur=Decimal("49.90"),
        )

        with self.assertRaisesMessage(PaymentGatewayError, "capture identifier"):
            request_subscription_cycle_refund(
                cycle,
                amount=Decimal("10.00"),
                actor=self.staff,
                reason="Refund requested",
                client=self.client,
            )

    def test_failed_refund_retry_reuses_the_same_provider_reference(self):
        cycle = SubscriptionCycle.objects.create(
            entitlement=self.entitlement,
            provider_cycle_id="retryable-refund-cycle",
            provider_capture_id="capture-retry",
            status=SubscriptionCycleStatus.PAID,
            amount_eur=Decimal("49.90"),
        )
        self.client.fail_refund = True
        with self.assertRaises(PaymentGatewayError):
            request_subscription_cycle_refund(
                cycle,
                amount=Decimal("15.00"),
                actor=self.staff,
                reason="First attempt",
                client=self.client,
            )
        cycle.refresh_from_db()
        failed_reference = cycle.refund_reference

        self.client.fail_refund = False
        request_subscription_cycle_refund(
            cycle,
            amount=Decimal("15.00"),
            actor=self.staff,
            reason="Retry",
            client=self.client,
        )

        cycle.refresh_from_db()
        self.assertEqual(cycle.refund_reference, failed_reference)
        self.assertEqual(
            [request[2] for request in self.client.refund_requests],
            [failed_reference, failed_reference],
        )

    def test_pending_refund_without_provider_id_is_resubmitted_idempotently(self):
        cycle = SubscriptionCycle.objects.create(
            entitlement=self.entitlement,
            provider_cycle_id="orphaned-provider-refund-cycle",
            provider_capture_id="capture-orphaned-refund",
            status=SubscriptionCycleStatus.PAID,
            amount_eur=Decimal("49.90"),
            refund_status=RefundStatus.PENDING,
            refund_amount_eur=Decimal("15.00"),
            refund_reference="cycle-refund-recovery-reference",
        )

        result = request_subscription_cycle_refund(
            cycle,
            amount=Decimal("15.00"),
            actor=self.staff,
            reason="Recover interrupted request",
            client=self.client,
        )

        self.assertEqual(
            self.client.refund_requests,
            [
                (
                    "capture-orphaned-refund",
                    Decimal("15.00"),
                    "cycle-refund-recovery-reference",
                )
            ],
        )
        self.assertEqual(result.provider_refund_id, "refund-1")

    def test_failed_refund_retry_rejects_a_different_amount(self):
        cycle = SubscriptionCycle.objects.create(
            entitlement=self.entitlement,
            provider_cycle_id="changed-amount-refund-cycle",
            provider_capture_id="capture-changed-amount",
            status=SubscriptionCycleStatus.PAID,
            amount_eur=Decimal("49.90"),
        )
        self.client.fail_refund = True
        with self.assertRaises(PaymentGatewayError):
            request_subscription_cycle_refund(
                cycle,
                amount=Decimal("15.00"),
                actor=self.staff,
                reason="First attempt",
                client=self.client,
            )
        cycle.refresh_from_db()

        with self.assertRaisesMessage(PaymentGatewayError, "originally requested amount"):
            request_subscription_cycle_refund(
                cycle,
                amount=Decimal("10.00"),
                actor=self.staff,
                reason="Changed retry",
                client=self.client,
            )

        self.assertEqual(len(self.client.refund_requests), 1)

    def test_pending_refund_is_reconciled_when_the_webhook_is_missing(self):
        cycle = SubscriptionCycle.objects.create(
            entitlement=self.entitlement,
            provider_cycle_id="missed-refund-webhook-cycle",
            provider_capture_id="capture-missed-webhook",
            status=SubscriptionCycleStatus.PAID,
            amount_eur=Decimal("49.90"),
        )
        requested = request_subscription_cycle_refund(
            cycle,
            amount=Decimal("20.00"),
            actor=self.staff,
            reason="Customer refund",
            client=self.client,
        )
        self.client.refund = {
            "id": "refund-1",
            "status": "success",
            "transaction_key": requested.refund_reference,
            "value": "20.00",
        }

        with patch(
            "activities.services.subscriptions.issue_activity_credit_note",
            return_value="credit-note-1",
        ) as issue_credit_note:
            result = reconcile_subscription_cycle_refund(requested, client=self.client)

        self.assertEqual(result.refund_status, RefundStatus.REFUNDED)
        issue_credit_note.assert_called_once()

    @patch("activities.services.subscriptions.issue_activity_credit_note")
    def test_failed_refund_confirmation_is_persisted_for_automatic_retry(
        self,
        issue_credit_note,
    ):
        cycle = SubscriptionCycle.objects.create(
            entitlement=self.entitlement,
            provider_cycle_id="failed-provider-refund-cycle",
            provider_capture_id="capture-failed-provider-refund",
            status=SubscriptionCycleStatus.PAID,
            amount_eur=Decimal("49.90"),
        )
        requested = request_subscription_cycle_refund(
            cycle,
            amount=Decimal("20.00"),
            actor=self.staff,
            reason="Customer refund",
            client=self.client,
        )
        self.client.refund = {
            "id": "refund-1",
            "status": "failed",
            "transaction_key": requested.refund_reference,
            "value": "20.00",
        }

        result = confirm_subscription_cycle_refund(
            requested,
            provider_refund_id="refund-1",
            client=self.client,
        )

        self.assertEqual(result.refund_status, RefundStatus.FAILED)
        issue_credit_note.assert_not_called()

    @patch("activities.services.subscriptions.issue_activity_document")
    def test_document_retry_skips_records_without_confirmed_captures(self, issue_document):
        pack_offering = create_offering(
            mode=OfferingMode.PACK,
            remote_id="unpaid-manual-retry-pack",
        )
        unpaid_pack = pack_offering.entitlements.create(
            customer=self.customer,
            pet=self.pet,
            status=EntitlementStatus.ACTIVE,
            item_name_snapshot=pack_offering.catalog_item.name,
            unit_price_eur_snapshot=pack_offering.price_eur,
            merchant_reference="unpaid-manual-retry-pack",
        )
        SubscriptionCycle.objects.create(
            entitlement=self.entitlement,
            provider_cycle_id="manual-retry-cycle-without-capture",
            status=SubscriptionCycleStatus.PAID,
            amount_eur=Decimal("49.90"),
        )

        pack_count = retry_subscription_documents(
            unpaid_pack,
            actor=self.staff,
            reason="Verify missing pack receipt",
        )
        subscription_count = retry_subscription_documents(
            self.entitlement,
            actor=self.staff,
            reason="Verify missing subscription receipt",
        )

        self.assertEqual(pack_count, 0)
        self.assertEqual(subscription_count, 0)
        issue_document.assert_not_called()
