import logging

from django.core.management.base import BaseCommand
from django.db.models import Q

from hotel.models import (
    Booking,
    BookingDocumentStatus,
    BookingRefund,
    BookingRefundStatus,
    PaymentStatus,
)
from hotel.services.invoicing import (
    issue_booking_document,
    issue_booking_refund_credit_note,
)
from hotel.services.payments import (
    issue_confirmed_booking_credit_note,
    refund_requires_booking_credit_note,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Retry TOConline documents for bookings with confirmed captures"

    def handle(self, *args, **options):
        failed = 0
        issued = 0
        bookings = (
            Booking.objects.filter(
                payment_attempts__provider_capture_id__gt="",
                payment_attempts__status__in=(
                    PaymentStatus.PAID,
                    PaymentStatus.REFUND_PENDING,
                    PaymentStatus.REFUND_FAILED,
                    PaymentStatus.REFUNDED,
                    PaymentStatus.DISPUTED,
                ),
            )
            .filter(
                Q(
                    document_status__in=(
                        BookingDocumentStatus.PENDING,
                        BookingDocumentStatus.FAILED,
                    )
                )
                | Q(
                    toconline_amend_document_id="",
                    payment_attempts__status=PaymentStatus.REFUNDED,
                )
            )
            .distinct()
        )

        for booking in bookings.prefetch_related("payment_attempts").iterator(chunk_size=200):
            document_needs_retry = booking.document_status in {
                BookingDocumentStatus.PENDING,
                BookingDocumentStatus.FAILED,
            }
            refunded_payment = next(
                (
                    payment
                    for payment in booking.payment_attempts.all()
                    if refund_requires_booking_credit_note(payment)
                ),
                None,
            )
            credit_note_needs_retry = bool(
                refunded_payment and not booking.toconline_amend_document_id
            )
            if not document_needs_retry and not credit_note_needs_retry:
                continue
            try:
                issue_booking_document(booking)
                if credit_note_needs_retry:
                    issue_confirmed_booking_credit_note(refunded_payment)
                issued += 1
            except Exception as exc:
                failed += 1
                logger.exception(
                    "TOConline document retry failed",
                    extra={"booking_id": booking.pk},
                )
                self.stderr.write(f"Booking {booking.pk}: {type(exc).__name__}; consult the logs")

        booking_refunds = BookingRefund.objects.filter(
            status=BookingRefundStatus.REFUNDED,
            toconline_amend_document_id="",
        ).select_related("payment_attempt__booking")
        for refund in booking_refunds.iterator():
            try:
                booking = refund.payment_attempt.booking
                if booking.document_status in {
                    BookingDocumentStatus.PENDING,
                    BookingDocumentStatus.FAILED,
                }:
                    issue_booking_document(booking)
                issue_booking_refund_credit_note(refund)
                issued += 1
            except Exception as exc:
                failed += 1
                logger.exception(
                    "TOConline booking refund document retry failed",
                    extra={"booking_refund_id": refund.pk},
                )
                self.stderr.write(
                    f"Booking refund {refund.pk}: {type(exc).__name__}; consult the logs"
                )

        self.stdout.write(f"Issued: {issued}; still failed: {failed}")
