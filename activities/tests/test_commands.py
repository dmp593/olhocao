from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from activities.models import (
    EntitlementRefund,
    EntitlementStatus,
    OfferingMode,
    RefundStatus,
    SubscriptionCycle,
    SubscriptionCycleStatus,
)
from activities.tests.factories import create_offering
from hotel.models import BookingDocumentStatus
from hotel.tests.factories import create_account, create_pet


class ActivityRecoveryCommandTests(TestCase):
    def setUp(self):
        customer = create_account()
        pet = create_pet(customer)
        offering = create_offering(mode=OfferingMode.SUBSCRIPTION)
        self.entitlement = offering.entitlements.create(
            customer=customer,
            pet=pet,
            status=EntitlementStatus.ACTIVE,
            item_name_snapshot=offering.catalog_item.name,
            unit_price_eur_snapshot=offering.price_eur,
            merchant_reference="recovery-subscription",
        )

    @patch("activities.management.commands.maintain_activities.expire_finished_entitlements")
    @patch("activities.management.commands.maintain_activities.issue_activity_credit_note")
    def test_confirmed_refund_retries_its_credit_note(
        self,
        issue_credit_note,
        expire_finished_entitlements,
    ):
        expire_finished_entitlements.return_value = 0
        cycle = SubscriptionCycle.objects.create(
            entitlement=self.entitlement,
            provider_cycle_id="refunded-cycle",
            status=SubscriptionCycleStatus.PAID,
            amount_eur=Decimal("49.90"),
            document_status=BookingDocumentStatus.ISSUED,
            toconline_sale_document_id="sale-document-1",
            refund_status=RefundStatus.REFUNDED,
            refund_amount_eur=Decimal("20.00"),
        )

        call_command("maintain_activities", stdout=StringIO())

        issue_credit_note.assert_called_once_with(cycle)

    @patch("activities.management.commands.maintain_activities.expire_finished_entitlements")
    @patch("activities.management.commands.maintain_activities.reconcile_subscription_cycle_refund")
    def test_pending_refund_is_reconciled(
        self,
        reconcile_refund,
        expire_finished_entitlements,
    ):
        expire_finished_entitlements.return_value = 0
        cycle = SubscriptionCycle.objects.create(
            entitlement=self.entitlement,
            provider_cycle_id="pending-refund-cycle",
            provider_capture_id="pending-capture",
            provider_refund_id="pending-refund",
            status=SubscriptionCycleStatus.PAID,
            amount_eur=Decimal("49.90"),
            refund_status=RefundStatus.PENDING,
            refund_amount_eur=Decimal("12.50"),
            refund_reference="pending-refund-reference",
        )
        reconcile_refund.return_value = cycle

        call_command("maintain_activities", stdout=StringIO())

        reconcile_refund.assert_called_once_with(cycle)

    @patch("activities.management.commands.maintain_activities.expire_finished_entitlements")
    @patch("activities.management.commands.maintain_activities.request_subscription_cycle_refund")
    def test_failed_refund_retries_with_its_original_amount(
        self,
        request_refund,
        expire_finished_entitlements,
    ):
        expire_finished_entitlements.return_value = 0
        cycle = SubscriptionCycle.objects.create(
            entitlement=self.entitlement,
            provider_cycle_id="failed-refund-cycle",
            provider_capture_id="capture-1",
            status=SubscriptionCycleStatus.PAID,
            amount_eur=Decimal("49.90"),
            refund_status=RefundStatus.FAILED,
            refund_amount_eur=Decimal("12.50"),
            refund_reference="preserved-refund-reference",
        )

        call_command("maintain_activities", stdout=StringIO())

        request_refund.assert_called_once_with(
            cycle,
            amount=Decimal("12.50"),
            actor=None,
            reason="Automatic retry after a failed refund request",
        )

    @patch("activities.management.commands.maintain_activities.expire_finished_entitlements")
    @patch("activities.management.commands.maintain_activities.request_subscription_cycle_refund")
    def test_interrupted_cycle_refund_request_is_resubmitted(
        self,
        request_refund,
        expire_finished_entitlements,
    ):
        expire_finished_entitlements.return_value = 0
        cycle = SubscriptionCycle.objects.create(
            entitlement=self.entitlement,
            provider_cycle_id="interrupted-cycle-refund",
            provider_capture_id="capture-interrupted-cycle",
            status=SubscriptionCycleStatus.PAID,
            amount_eur=Decimal("49.90"),
            refund_status=RefundStatus.PENDING,
            refund_amount_eur=Decimal("12.50"),
            refund_reference="interrupted-cycle-reference",
            provider_refund_id="",
        )

        call_command("maintain_activities", stdout=StringIO())

        request_refund.assert_called_once_with(
            cycle,
            amount=Decimal("12.50"),
            actor=None,
            reason="Automatic retry after a failed refund request",
        )

    @patch("activities.management.commands.maintain_activities.expire_finished_entitlements")
    @patch("activities.management.commands.maintain_activities.retry_entitlement_refund_request")
    def test_interrupted_purchase_refund_request_is_resubmitted(
        self,
        retry_refund,
        expire_finished_entitlements,
    ):
        expire_finished_entitlements.return_value = 0
        refund = EntitlementRefund.objects.create(
            entitlement=self.entitlement,
            amount_eur=Decimal("12.50"),
            status=RefundStatus.PENDING,
            reason="Interrupted request",
            refund_reference="interrupted-purchase-refund",
            provider_refund_id="",
        )

        call_command("maintain_activities", stdout=StringIO())

        retry_refund.assert_called_once_with(refund)

    @patch("activities.management.commands.maintain_activities.expire_finished_entitlements")
    @patch("activities.management.commands.maintain_activities.issue_activity_document_safely")
    def test_unpaid_records_are_not_sent_to_toconline(
        self,
        issue_document,
        expire_finished_entitlements,
    ):
        expire_finished_entitlements.return_value = 0
        pack_offering = create_offering(
            mode=OfferingMode.PACK,
            remote_id="unpaid-recovery-pack",
        )
        pack_offering.entitlements.create(
            customer=self.entitlement.customer,
            pet=self.entitlement.pet,
            status=EntitlementStatus.ACTIVE,
            item_name_snapshot=pack_offering.catalog_item.name,
            unit_price_eur_snapshot=pack_offering.price_eur,
            merchant_reference="unpaid-recovery-pack",
            document_status=BookingDocumentStatus.PENDING,
        )
        SubscriptionCycle.objects.create(
            entitlement=self.entitlement,
            provider_cycle_id="cycle-without-capture",
            status=SubscriptionCycleStatus.PAID,
            amount_eur=Decimal("49.90"),
            document_status=BookingDocumentStatus.PENDING,
        )

        call_command("maintain_activities", stdout=StringIO())

        issue_document.assert_not_called()
