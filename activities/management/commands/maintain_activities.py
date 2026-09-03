import logging

from django.core.management.base import BaseCommand
from django.db.models import Q

from activities.models import (
    EntitlementRefund,
    EntitlementStatus,
    RefundStatus,
    ServiceEntitlement,
    SubscriptionCycle,
    SubscriptionCycleStatus,
)
from activities.services.billing import issue_activity_document_safely
from activities.services.invoicing import (
    issue_activity_credit_note,
    issue_entitlement_refund_credit_note,
)
from activities.services.refunds import (
    reconcile_entitlement_refund,
    retry_entitlement_refund_request,
)
from activities.services.reservations import expire_finished_entitlements
from activities.services.subscriptions import (
    reconcile_subscription_cycle_refund,
    request_subscription_cycle_refund,
)
from hotel.models import BookingDocumentStatus
from hotel.services.payments import PaymentGatewayError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Expire finished activity access and retry TOConline documents safely."

    def handle(self, *args, **options):
        expired_count = expire_finished_entitlements()
        retried_count = 0
        entitlements = ServiceEntitlement.objects.filter(
            status=EntitlementStatus.ACTIVE,
            offering__mode__in=("one_off", "pack"),
            paid_at__isnull=False,
            provider_capture_id__gt="",
            document_status__in=(BookingDocumentStatus.PENDING, BookingDocumentStatus.FAILED),
        )
        for entitlement in entitlements.iterator():
            issue_activity_document_safely(entitlement)
            retried_count += 1

        cycles = SubscriptionCycle.objects.filter(
            status=SubscriptionCycleStatus.PAID,
            provider_capture_id__gt="",
            document_status__in=(BookingDocumentStatus.PENDING, BookingDocumentStatus.FAILED),
        ).select_related("entitlement")
        for cycle in cycles.iterator():
            issue_activity_document_safely(cycle.entitlement, cycle=cycle)
            retried_count += 1

        refund_reconciliation_count = 0
        pending_refunds = SubscriptionCycle.objects.filter(
            refund_status=RefundStatus.PENDING,
            provider_refund_id__gt="",
        )
        for cycle in pending_refunds.iterator():
            try:
                result = reconcile_subscription_cycle_refund(cycle)
            except Exception:
                logger.exception(
                    "Easypay subscription refund reconciliation failed",
                    extra={"cycle_id": cycle.pk},
                )
            else:
                if result.refund_status == RefundStatus.REFUNDED:
                    refund_reconciliation_count += 1

        pending_purchase_refunds = EntitlementRefund.objects.filter(
            status=RefundStatus.PENDING,
            provider_refund_id__gt="",
        )
        for refund in pending_purchase_refunds.iterator():
            try:
                result = reconcile_entitlement_refund(refund)
            except Exception:
                logger.exception(
                    "Easypay activity purchase refund reconciliation failed",
                    extra={"refund_id": refund.pk},
                )
            else:
                if result.status == RefundStatus.REFUNDED:
                    refund_reconciliation_count += 1

        credit_note_retry_count = 0
        refunded_cycles = SubscriptionCycle.objects.filter(
            refund_status=RefundStatus.REFUNDED,
            toconline_amend_document_id="",
        )
        for cycle in refunded_cycles.iterator():
            try:
                issue_activity_credit_note(cycle)
            except Exception:
                logger.exception(
                    "TOConline subscription credit note retry failed",
                    extra={"cycle_id": cycle.pk},
                )
            else:
                credit_note_retry_count += 1

        refunded_purchases = EntitlementRefund.objects.filter(
            status=RefundStatus.REFUNDED,
            toconline_amend_document_id="",
        )
        for refund in refunded_purchases.iterator():
            try:
                issue_entitlement_refund_credit_note(refund)
            except Exception:
                logger.exception(
                    "TOConline activity purchase credit note retry failed",
                    extra={"refund_id": refund.pk},
                )
            else:
                credit_note_retry_count += 1

        refund_retry_count = 0
        failed_refunds = SubscriptionCycle.objects.filter(
            Q(refund_status=RefundStatus.FAILED)
            | Q(refund_status=RefundStatus.PENDING, provider_refund_id=""),
            refund_amount_eur__isnull=False,
        ).select_related("entitlement")
        for cycle in failed_refunds.iterator():
            try:
                request_subscription_cycle_refund(
                    cycle,
                    amount=cycle.refund_amount_eur,
                    actor=None,
                    reason="Automatic retry after a failed refund request",
                )
            except PaymentGatewayError as exc:
                self.stderr.write(f"Refund retry failed for cycle {cycle.pk}: {exc}")
            else:
                refund_retry_count += 1

        failed_purchase_refunds = EntitlementRefund.objects.filter(
            Q(status=RefundStatus.FAILED) | Q(status=RefundStatus.PENDING, provider_refund_id=""),
        ).select_related("entitlement")
        for refund in failed_purchase_refunds.iterator():
            try:
                retry_entitlement_refund_request(refund)
            except PaymentGatewayError as exc:
                self.stderr.write(f"Refund retry failed for purchase {refund.pk}: {exc}")
            else:
                refund_retry_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Expired {expired_count} entitlement(s); retried {retried_count} document(s); "
                f"reconciled {refund_reconciliation_count} refund(s); "
                f"retried {refund_retry_count} refund(s); "
                f"retried {credit_note_retry_count} credit note(s)."
            )
        )
