import logging
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from hotel.models import (
    BookingEvent,
    BookingEventType,
    BookingRefund,
    BookingRefundStatus,
    PaymentStatus,
)
from hotel.services.invoicing import issue_booking_refund_credit_note
from hotel.services.payments import EasypayClient, PaymentGatewayError

logger = logging.getLogger(__name__)


def refundable_booking_balance(payment_attempt):
    """Return the unreserved balance of the captured booking payment."""

    reserved = payment_attempt.booking_refunds.filter(
        status__in=(BookingRefundStatus.PENDING, BookingRefundStatus.REFUNDED),
    ).aggregate(total=Sum("amount_eur"))["total"] or Decimal("0.00")
    return max(payment_attempt.amount_eur - reserved, Decimal("0.00"))


def request_booking_refund(
    payment_attempt,
    *,
    amount,
    actor,
    reason,
    client=None,
):
    """Reserve and submit one auditable total or partial booking refund."""

    client = client or EasypayClient()
    amount = _money(amount)
    reason = str(reason or "").strip()
    if not reason:
        raise PaymentGatewayError(_("A reason is required for the refund audit history."))

    with transaction.atomic():
        locked_payment = (
            payment_attempt.__class__.objects.select_for_update()
            .select_related("booking")
            .get(pk=payment_attempt.pk)
        )
        if locked_payment.status != PaymentStatus.PAID:
            raise PaymentGatewayError(_("Only a confirmed payment can be refunded."))
        if not locked_payment.provider_capture_id:
            raise PaymentGatewayError(
                _("The provider capture identifier is unavailable; reconcile the payment first.")
            )
        if amount <= Decimal("0.00") or amount > refundable_booking_balance(locked_payment):
            raise PaymentGatewayError(_("The refund amount exceeds the refundable balance."))

        refund = BookingRefund.objects.create(
            payment_attempt=locked_payment,
            amount_eur=amount,
            status=BookingRefundStatus.PENDING,
            reason=reason,
            refund_reference=f"booking-refund-{locked_payment.booking_id}-{uuid4().hex[:12]}",
            requested_by=actor,
        )
        _record_refund_event(refund, BookingEventType.REFUND_REQUESTED, actor=actor)

    return _submit_booking_refund(refund, client=client)


def retry_booking_refund_request(refund, *, actor=None, client=None):
    """Retry a failed/interrupted provider request with the original key."""

    client = client or EasypayClient()
    with transaction.atomic():
        locked = (
            BookingRefund.objects.select_for_update()
            .select_related("payment_attempt__booking")
            .get(pk=refund.pk)
        )
        if locked.status == BookingRefundStatus.REFUNDED:
            return locked
        if locked.status == BookingRefundStatus.PENDING and locked.provider_refund_id:
            return locked
        if locked.status not in {
            BookingRefundStatus.PENDING,
            BookingRefundStatus.FAILED,
        }:
            raise PaymentGatewayError(_("This refund cannot be retried in its current state."))
        if not locked.payment_attempt.provider_capture_id:
            raise PaymentGatewayError(_("The provider capture identifier is unavailable."))
        if locked.status == BookingRefundStatus.FAILED:
            available = refundable_booking_balance(locked.payment_attempt)
            if locked.amount_eur > available:
                raise PaymentGatewayError(_("The refund amount exceeds the refundable balance."))
            locked.status = BookingRefundStatus.PENDING
            locked.provider_error = ""
            locked.save(update_fields=("status", "provider_error"))
        _record_refund_event(
            locked,
            BookingEventType.REFUND_REQUESTED,
            actor=actor,
            extra={"retry": True},
        )

    return _submit_booking_refund(locked, client=client)


def confirm_booking_refund(refund, *, provider_refund_id, client=None):
    """Verify provider state before recording refund and fiscal side effects."""

    client = client or EasypayClient()
    provider_refund = client.get_refund(provider_refund_id)
    if str(provider_refund.get("transaction_key") or "") != refund.refund_reference:
        raise PaymentGatewayError("The refund reference does not match.")
    if _money(provider_refund.get("value")) != refund.amount_eur:
        raise PaymentGatewayError("The refund amount does not match.")

    provider_status = str(provider_refund.get("status") or "").lower()
    if provider_status in {"pending", "waiting"}:
        return refund
    if provider_status != "success":
        BookingRefund.objects.filter(pk=refund.pk).update(
            status=BookingRefundStatus.FAILED,
            provider_error="Easypay reported a failed refund.",
        )
        refund.refresh_from_db()
        _record_refund_event(refund, BookingEventType.REFUND_FAILED)
        return refund

    with transaction.atomic():
        locked = (
            BookingRefund.objects.select_for_update()
            .select_related("payment_attempt__booking")
            .get(pk=refund.pk)
        )
        if locked.status == BookingRefundStatus.REFUNDED:
            return locked
        locked.status = BookingRefundStatus.REFUNDED
        locked.provider_refund_id = provider_refund_id
        locked.provider_error = ""
        locked.refunded_at = locked.refunded_at or timezone.now()
        locked.save(
            update_fields=(
                "status",
                "provider_refund_id",
                "provider_error",
                "refunded_at",
            )
        )
        _record_refund_event(locked, BookingEventType.REFUND_CONFIRMED)

    try:
        issue_booking_refund_credit_note(locked)
    except Exception as exc:
        logger.exception(
            "TOConline credit note failed after confirmed booking refund",
            extra={
                "booking_id": locked.payment_attempt.booking_id,
                "booking_refund_id": locked.pk,
            },
        )
        locked.document_error = f"{type(exc).__name__}: {str(exc)[:500]}"
        locked.save(update_fields=("document_error",))
    return locked


def reconcile_booking_refund(refund, *, client=None):
    """Recover a confirmed provider refund whose webhook was not delivered."""

    refund = BookingRefund.objects.get(pk=refund.pk)
    if refund.status != BookingRefundStatus.PENDING:
        return refund
    if not refund.provider_refund_id:
        raise PaymentGatewayError(_("The provider refund identifier is unavailable."))
    return confirm_booking_refund(
        refund,
        provider_refund_id=refund.provider_refund_id,
        client=client,
    )


def _submit_booking_refund(refund, *, client):
    try:
        response = client.create_refund(
            refund.payment_attempt.provider_capture_id,
            amount=refund.amount_eur,
            reference=refund.refund_reference,
        )
    except PaymentGatewayError as exc:
        BookingRefund.objects.filter(pk=refund.pk).update(
            status=BookingRefundStatus.FAILED,
            provider_error=str(exc),
        )
        refund.refresh_from_db()
        _record_refund_event(refund, BookingEventType.REFUND_FAILED)
        raise

    provider_refund_id = str(response.get("id") or "")
    if not provider_refund_id:
        message = _("Easypay returned an incomplete refund response.")
        BookingRefund.objects.filter(pk=refund.pk).update(
            status=BookingRefundStatus.FAILED,
            provider_refund_id="",
            provider_error=str(message),
        )
        refund.refresh_from_db()
        _record_refund_event(refund, BookingEventType.REFUND_FAILED)
        raise PaymentGatewayError(message)

    BookingRefund.objects.filter(pk=refund.pk).update(
        provider_refund_id=provider_refund_id,
        provider_error="",
    )
    refund.refresh_from_db()
    return refund


def _record_refund_event(refund, event_type, *, actor=None, extra=None):
    details = {
        "booking_refund_id": refund.pk,
        "payment_attempt_id": refund.payment_attempt_id,
        "amount_eur": str(refund.amount_eur),
        "reason": refund.reason,
    }
    details.update(extra or {})
    BookingEvent.objects.create(
        booking_id=refund.payment_attempt.booking_id,
        event_type=event_type,
        actor=actor,
        details=details,
    )


def _money(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise PaymentGatewayError(_("The refund amount is invalid.")) from None
