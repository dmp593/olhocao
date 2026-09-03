import logging
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from activities.models import (
    CreditLedgerEntry,
    EntitlementRefund,
    EntitlementStatus,
    LedgerReason,
    OfferingMode,
    RefundStatus,
    ServiceEntitlement,
    ServiceFulfilment,
    ServiceFulfilmentEvent,
    ServiceFulfilmentStatus,
    SubscriptionManagementAction,
    SubscriptionManagementEvent,
)
from activities.services.invoicing import issue_entitlement_refund_credit_note
from hotel.services.payments import EasypayClient, PaymentGatewayError

logger = logging.getLogger(__name__)


def request_entitlement_refund(
    entitlement,
    *,
    amount,
    actor,
    reason,
    revoke_remaining_access=False,
    client=None,
):
    """Request a total or partial refund without rewriting the original sale."""

    client = client or EasypayClient()
    amount = _money(amount)
    reason = str(reason or "").strip()
    if not reason:
        raise PaymentGatewayError(_("A reason is required for the refund audit history."))

    with transaction.atomic():
        locked = ServiceEntitlement.objects.select_for_update().get(pk=entitlement.pk)
        if locked.offering_mode_snapshot not in {OfferingMode.ONE_OFF, OfferingMode.PACK}:
            raise PaymentGatewayError(
                _("Recurring payments are refunded from their billing cycle.")
            )
        if not locked.paid_at or not locked.provider_capture_id:
            raise PaymentGatewayError(_("This purchase has no confirmed payment capture."))
        already_reserved = locked.refunds.filter(
            status__in=(RefundStatus.PENDING, RefundStatus.REFUNDED)
        ).aggregate(total=Sum("amount_eur"))["total"] or Decimal("0.00")
        remaining = locked.total_price_eur - already_reserved
        if amount <= Decimal("0.00") or amount > remaining:
            raise PaymentGatewayError(_("The refund amount exceeds the refundable balance."))

        refund = EntitlementRefund.objects.create(
            entitlement=locked,
            amount_eur=amount,
            status=RefundStatus.PENDING,
            reason=reason,
            revoke_remaining_access=revoke_remaining_access,
            refund_reference=f"activity-purchase-refund-{locked.pk}-{uuid4().hex[:12]}",
            requested_by=actor,
        )
        _record_refund_request(refund, actor=actor, retry=False)

    return _submit_entitlement_refund(refund, client=client)


def retry_entitlement_refund_request(refund, *, actor=None, client=None):
    """Retry one failed provider request using its original idempotency key."""

    client = client or EasypayClient()
    with transaction.atomic():
        locked = (
            EntitlementRefund.objects.select_for_update()
            .select_related("entitlement")
            .get(pk=refund.pk)
        )
        if locked.status == RefundStatus.REFUNDED:
            return locked
        if locked.status == RefundStatus.PENDING and locked.provider_refund_id:
            return locked
        if locked.status not in {RefundStatus.PENDING, RefundStatus.FAILED}:
            raise PaymentGatewayError(_("This refund cannot be retried in its current state."))
        if not locked.entitlement.provider_capture_id:
            raise PaymentGatewayError(_("The provider capture identifier is unavailable."))
        if locked.status == RefundStatus.FAILED:
            locked.status = RefundStatus.PENDING
            locked.provider_error = ""
            locked.save(update_fields=("status", "provider_error"))
        _record_refund_request(locked, actor=actor, retry=True)

    return _submit_entitlement_refund(locked, client=client)


def confirm_entitlement_refund(refund, *, provider_refund_id, client=None):
    """Verify provider state, then apply local access and fiscal side effects."""

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
        EntitlementRefund.objects.filter(pk=refund.pk).update(
            status=RefundStatus.FAILED,
            provider_error="Easypay reported a failed refund.",
        )
        refund.refresh_from_db()
        return refund

    with transaction.atomic():
        locked_refund = (
            EntitlementRefund.objects.select_for_update()
            .select_related("entitlement")
            .get(pk=refund.pk)
        )
        if locked_refund.status == RefundStatus.REFUNDED:
            return locked_refund
        locked_refund.status = RefundStatus.REFUNDED
        locked_refund.provider_refund_id = provider_refund_id
        locked_refund.provider_error = ""
        locked_refund.refunded_at = locked_refund.refunded_at or timezone.now()
        locked_refund.save(
            update_fields=(
                "status",
                "provider_refund_id",
                "provider_error",
                "refunded_at",
            )
        )
        if locked_refund.revoke_remaining_access:
            _revoke_remaining_access(locked_refund)

    try:
        issue_entitlement_refund_credit_note(locked_refund)
    except Exception as exc:
        logger.exception(
            "TOConline credit note failed after confirmed activity purchase refund",
            extra={"entitlement_id": locked_refund.entitlement_id, "refund_id": locked_refund.pk},
        )
        locked_refund.document_error = f"{type(exc).__name__}: {str(exc)[:500]}"
        locked_refund.save(update_fields=("document_error",))
    return locked_refund


def reconcile_entitlement_refund(refund, *, client=None):
    refund = EntitlementRefund.objects.get(pk=refund.pk)
    if refund.status != RefundStatus.PENDING:
        return refund
    if not refund.provider_refund_id:
        raise PaymentGatewayError(_("The provider refund identifier is unavailable."))
    return confirm_entitlement_refund(
        refund,
        provider_refund_id=refund.provider_refund_id,
        client=client,
    )


def _submit_entitlement_refund(refund, *, client):
    try:
        response = client.create_refund(
            refund.entitlement.provider_capture_id,
            amount=refund.amount_eur,
            reference=refund.refund_reference,
        )
    except PaymentGatewayError as exc:
        EntitlementRefund.objects.filter(pk=refund.pk).update(
            status=RefundStatus.FAILED,
            provider_error=str(exc),
        )
        refund.refresh_from_db()
        raise

    provider_refund_id = str(response.get("id") or "")
    if not provider_refund_id:
        message = _("Easypay returned an incomplete refund response.")
        EntitlementRefund.objects.filter(pk=refund.pk).update(
            status=RefundStatus.FAILED,
            provider_refund_id="",
            provider_error=str(message),
        )
        refund.refresh_from_db()
        raise PaymentGatewayError(message)
    EntitlementRefund.objects.filter(pk=refund.pk).update(
        provider_refund_id=provider_refund_id,
        provider_error="",
    )
    refund.refresh_from_db()
    return refund


def _record_refund_request(refund, *, actor, retry):
    entitlement = refund.entitlement
    SubscriptionManagementEvent.objects.create(
        entitlement=entitlement,
        action=SubscriptionManagementAction.REFUND_REQUESTED,
        actor=actor,
        from_status=entitlement.status,
        to_status=entitlement.status,
        reason=refund.reason,
        details={
            "purchase_refund_id": refund.pk,
            "amount_eur": str(refund.amount_eur),
            "revoke_remaining_access": refund.revoke_remaining_access,
            "retry": retry,
        },
    )


def _revoke_remaining_access(refund):
    entitlement = ServiceEntitlement.objects.select_for_update().get(pk=refund.entitlement_id)
    previous_status = entitlement.status
    now = timezone.now()
    entitlement.status = EntitlementStatus.CANCELLED
    entitlement.cancelled_at = entitlement.cancelled_at or now
    entitlement.access_blocked_at = entitlement.access_blocked_at or now
    entitlement.access_blocked_reason = _("Remaining access revoked with refund #%(id)s.") % {
        "id": refund.pk
    }
    entitlement.save(
        update_fields=(
            "status",
            "cancelled_at",
            "access_blocked_at",
            "access_blocked_reason",
            "updated_at",
        )
    )
    balance = entitlement.credit_entries.aggregate(total=Sum("delta"))["total"] or 0
    if balance > 0:
        CreditLedgerEntry.objects.get_or_create(
            idempotency_key=f"purchase-refund-revoke-{refund.pk}",
            defaults={
                "entitlement": entitlement,
                "delta": -balance,
                "reason": LedgerReason.ADJUSTMENT,
                "note": refund.reason,
                "created_by": refund.requested_by,
            },
        )
    open_fulfilments = ServiceFulfilment.objects.select_for_update().filter(
        entitlement=entitlement,
        status__in=(
            ServiceFulfilmentStatus.REQUESTED,
            ServiceFulfilmentStatus.SCHEDULED,
        ),
    )
    for fulfilment in open_fulfilments:
        from_status = fulfilment.status
        fulfilment.status = ServiceFulfilmentStatus.CANCELLED
        fulfilment.cancelled_at = now
        fulfilment.cancellation_reason = refund.reason
        fulfilment.cancellation_policy_overridden = True
        fulfilment.save(
            update_fields=(
                "status",
                "cancelled_at",
                "cancellation_reason",
                "cancellation_policy_overridden",
                "updated_at",
            )
        )
        ServiceFulfilmentEvent.objects.create(
            fulfilment=fulfilment,
            actor=refund.requested_by,
            from_status=from_status,
            to_status=fulfilment.status,
            reason=_("Remaining service cancelled after a confirmed refund."),
            details={"purchase_refund_id": refund.pk},
        )
    SubscriptionManagementEvent.objects.create(
        entitlement=entitlement,
        action=SubscriptionManagementAction.ACCESS_BLOCKED,
        actor=refund.requested_by,
        from_status=previous_status,
        to_status=entitlement.status,
        reason=refund.reason,
        details={"purchase_refund_id": refund.pk},
    )


def _money(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError) as exc:
        raise PaymentGatewayError(_("The refund amount is invalid.")) from exc
