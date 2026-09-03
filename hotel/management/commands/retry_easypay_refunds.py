import logging

from django.core.management.base import BaseCommand
from django.db.models import Q

from hotel.models import (
    BookingRefund,
    BookingRefundStatus,
    PaymentAttempt,
    PaymentStatus,
)
from hotel.services.payments import (
    PaymentGatewayError,
    reconcile_pending_refund,
    request_refund,
)
from hotel.services.refunds import (
    reconcile_booking_refund,
    retry_booking_refund_request,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Retry failed Easypay refunds without changing their original references"

    def handle(self, *args, **options):
        reconciled = 0
        retried = 0
        failed = 0
        pending_booking_refunds = BookingRefund.objects.filter(
            status=BookingRefundStatus.PENDING,
            provider_refund_id__gt="",
        )
        for refund in pending_booking_refunds.iterator():
            try:
                result = reconcile_booking_refund(refund)
            except PaymentGatewayError:
                failed += 1
                logger.exception(
                    "Booking refund reconciliation failed",
                    extra={"booking_refund_id": refund.pk},
                )
            else:
                if result.status == BookingRefundStatus.REFUNDED:
                    reconciled += 1

        interrupted_booking_refunds = BookingRefund.objects.filter(
            Q(status=BookingRefundStatus.FAILED)
            | Q(
                status=BookingRefundStatus.PENDING,
                provider_refund_id="",
            )
        )
        for refund in interrupted_booking_refunds.iterator():
            try:
                retry_booking_refund_request(refund)
                retried += 1
            except PaymentGatewayError:
                failed += 1
                logger.exception(
                    "Booking refund retry failed",
                    extra={"booking_refund_id": refund.pk},
                )

        pending_payments = PaymentAttempt.objects.filter(
            status=PaymentStatus.REFUND_PENDING,
            provider_refund_id__gt="",
        )
        for payment in pending_payments.iterator():
            try:
                result = reconcile_pending_refund(payment)
            except PaymentGatewayError:
                failed += 1
                logger.exception(
                    "Easypay refund reconciliation failed",
                    extra={
                        "booking_id": payment.booking_id,
                        "payment_id": payment.pk,
                    },
                )
            else:
                if result.status == PaymentStatus.REFUNDED:
                    reconciled += 1

        payments = PaymentAttempt.objects.filter(
            Q(status=PaymentStatus.REFUND_FAILED)
            | Q(status=PaymentStatus.REFUND_PENDING, provider_refund_id=""),
            refund_amount_eur__isnull=False,
        )

        for payment in payments.iterator():
            try:
                request_refund(payment, payment.refund_amount_eur)
                retried += 1
            except PaymentGatewayError:
                failed += 1
                logger.exception(
                    "Easypay refund retry failed",
                    extra={
                        "booking_id": payment.booking_id,
                        "payment_id": payment.pk,
                    },
                )

        self.stdout.write(f"Reconciled: {reconciled}; retried: {retried}; still failed: {failed}")
