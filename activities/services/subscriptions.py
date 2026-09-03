from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from activities.models import (
    EntitlementStatus,
    OfferingMode,
    RefundStatus,
    ServiceEntitlement,
    SubscriptionCycle,
    SubscriptionManagementAction,
    SubscriptionManagementEvent,
)
from activities.services.invoicing import issue_activity_credit_note, issue_activity_document
from hotel.services.payments import EasypayClient, PaymentGatewayError

MANAGEABLE_SUBSCRIPTION_STATUSES = (
    EntitlementStatus.PENDING,
    EntitlementStatus.ACTIVE,
    EntitlementStatus.PAST_DUE,
    EntitlementStatus.CANCEL_AT_PERIOD_END,
    EntitlementStatus.SUSPENDED,
)


def suspend_subscription(entitlement, *, actor, reason, client=None):
    """Stop future provider cycles and block access until an explicit resume."""
    client = client or EasypayClient()
    with transaction.atomic():
        locked = _locked_subscription(entitlement)
        if locked.status not in {
            EntitlementStatus.ACTIVE,
            EntitlementStatus.PAST_DUE,
            EntitlementStatus.CANCEL_AT_PERIOD_END,
        }:
            raise PaymentGatewayError(
                _("This subscription cannot be suspended in its current state.")
            )
        _stop_provider_subscription(locked, client)
        previous_status = locked.status
        locked.status = EntitlementStatus.SUSPENDED
        locked.suspended_at = timezone.now()
        locked.resume_scheduled_at = None
        locked.save(
            update_fields=(
                "status",
                "suspended_at",
                "resume_scheduled_at",
                "updated_at",
            )
        )
        _record_event(
            locked,
            action=SubscriptionManagementAction.SUSPENDED,
            actor=actor,
            from_status=previous_status,
            reason=reason,
        )
    return locked


def schedule_subscription_resume(entitlement, *, actor, reason, resume_at, client=None):
    """Reactivate provider billing while access waits for the next successful cycle."""
    client = client or EasypayClient()
    if resume_at <= timezone.now():
        raise PaymentGatewayError(_("The resume date must be in the future."))
    with transaction.atomic():
        locked = _locked_subscription(entitlement)
        if locked.status != EntitlementStatus.SUSPENDED:
            raise PaymentGatewayError(_("Only a suspended subscription can be resumed."))
        if not locked.provider_subscription_id:
            raise PaymentGatewayError(_("The provider subscription identifier is unavailable."))
        client.resume_subscription(
            locked.provider_subscription_id,
            frequency=locked.billing_frequency_snapshot,
            start_at=resume_at,
        )
        locked.resume_scheduled_at = resume_at
        locked.save(update_fields=("resume_scheduled_at", "updated_at"))
        _record_event(
            locked,
            action=SubscriptionManagementAction.RESUME_SCHEDULED,
            actor=actor,
            from_status=locked.status,
            reason=reason,
            details={"resume_at": resume_at.isoformat()},
        )
    return locked


def cancel_subscription_at_period_end(entitlement, *, actor, reason, client=None):
    """Stop renewals while preserving access that has already been paid for."""
    client = client or EasypayClient()
    with transaction.atomic():
        locked = _locked_subscription(entitlement)
        if locked.status == EntitlementStatus.CANCEL_AT_PERIOD_END:
            return locked
        if locked.status not in MANAGEABLE_SUBSCRIPTION_STATUSES:
            return locked
        _stop_provider_subscription(locked, client)
        now = timezone.now()
        previous_status = locked.status
        locked.cancelled_at = now
        locked.resume_scheduled_at = None
        if previous_status == EntitlementStatus.SUSPENDED and not locked.access_blocked_at:
            locked.access_blocked_at = locked.suspended_at or now
            locked.access_blocked_reason = _(
                "Access remained blocked because the subscription was suspended "
                "before cancellation."
            )
        locked.status = (
            EntitlementStatus.CANCEL_AT_PERIOD_END
            if locked.current_period_end and locked.current_period_end > now
            else EntitlementStatus.CANCELLED
        )
        locked.save(
            update_fields=(
                "status",
                "cancelled_at",
                "resume_scheduled_at",
                "access_blocked_at",
                "access_blocked_reason",
                "updated_at",
            )
        )
        _record_event(
            locked,
            action=SubscriptionManagementAction.CANCELLATION_SCHEDULED,
            actor=actor,
            from_status=previous_status,
            reason=reason,
        )
        if previous_status == EntitlementStatus.PENDING:
            _release_unpaid_promotion(locked)
    return locked


def terminate_subscription(entitlement, *, actor, reason, client=None):
    """Stop billing and access immediately without rewriting any paid cycle."""
    client = client or EasypayClient()
    with transaction.atomic():
        locked = _locked_subscription(entitlement)
        if locked.status not in MANAGEABLE_SUBSCRIPTION_STATUSES:
            raise PaymentGatewayError(_("This subscription is already closed."))
        _stop_provider_subscription(locked, client)
        now = timezone.now()
        previous_status = locked.status
        locked.status = EntitlementStatus.CANCELLED
        locked.cancelled_at = now
        locked.current_period_end = min(locked.current_period_end or now, now)
        locked.resume_scheduled_at = None
        locked.save(
            update_fields=(
                "status",
                "cancelled_at",
                "current_period_end",
                "resume_scheduled_at",
                "updated_at",
            )
        )
        _record_event(
            locked,
            action=SubscriptionManagementAction.TERMINATED,
            actor=actor,
            from_status=previous_status,
            reason=reason,
        )
        if previous_status == EntitlementStatus.PENDING:
            _release_unpaid_promotion(locked)
    return locked


def set_entitlement_access_block(entitlement, *, actor, reason, blocked):
    """Apply an audited operational hold to a pack or subscription."""
    with transaction.atomic():
        locked = ServiceEntitlement.objects.select_for_update().get(pk=entitlement.pk)
        previous_status = locked.status
        if blocked:
            if locked.access_blocked_at:
                return locked
            locked.access_blocked_at = timezone.now()
            locked.access_blocked_reason = reason
            action = SubscriptionManagementAction.ACCESS_BLOCKED
        else:
            if not locked.access_blocked_at:
                return locked
            locked.access_blocked_at = None
            locked.access_blocked_reason = ""
            action = SubscriptionManagementAction.ACCESS_RESTORED
        locked.save(
            update_fields=(
                "access_blocked_at",
                "access_blocked_reason",
                "updated_at",
            )
        )
        _record_event(
            locked,
            action=action,
            actor=actor,
            from_status=previous_status,
            reason=reason,
        )
    return locked


def set_subscription_access_block(entitlement, *, actor, reason, blocked):
    """Backward-compatible, intention-revealing entry point for subscription views."""
    if entitlement.offering_mode_snapshot != OfferingMode.SUBSCRIPTION:
        raise PaymentGatewayError(_("Only subscriptions can be managed this way."))
    return set_entitlement_access_block(
        entitlement,
        actor=actor,
        reason=reason,
        blocked=blocked,
    )


def synchronize_subscription(entitlement, *, actor, reason, client=None):
    client = client or EasypayClient()
    with transaction.atomic():
        locked = _locked_subscription(entitlement)
        if not locked.provider_subscription_id:
            raise PaymentGatewayError(_("The provider subscription identifier is unavailable."))
        response = client.get_subscription(locked.provider_subscription_id)
        provider_data = response.get("message", response) if isinstance(response, dict) else {}
        provider_status = str(provider_data.get("status") or "").lower()
        previous_status = locked.status
        now = timezone.now()
        if provider_status == "inactive" and locked.status not in {
            EntitlementStatus.SUSPENDED,
            EntitlementStatus.CANCEL_AT_PERIOD_END,
            EntitlementStatus.CANCELLED,
        }:
            locked.status = (
                EntitlementStatus.CANCEL_AT_PERIOD_END
                if locked.current_period_end and locked.current_period_end > now
                else EntitlementStatus.CANCELLED
            )
        locked.provider_synced_at = now
        locked.save(update_fields=("status", "provider_synced_at", "updated_at"))
        _record_event(
            locked,
            action=SubscriptionManagementAction.PROVIDER_SYNCED,
            actor=actor,
            from_status=previous_status,
            reason=reason,
            details={"provider_status": provider_status or "unknown"},
        )
        if (
            previous_status == EntitlementStatus.PENDING
            and locked.status == EntitlementStatus.CANCELLED
        ):
            _release_unpaid_promotion(locked)
    return locked


def request_subscription_cycle_refund(cycle, *, amount, actor, reason, client=None):
    """Request a provider refund without modifying the original paid cycle."""
    client = client or EasypayClient()
    try:
        amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError) as exc:
        raise PaymentGatewayError(_("The refund amount is invalid.")) from exc
    if not Decimal("0.00") < amount <= cycle.amount_eur:
        raise PaymentGatewayError(_("The refund amount is invalid."))
    if not cycle.provider_capture_id:
        raise PaymentGatewayError(
            _("The provider capture identifier is unavailable. Synchronize the subscription first.")
        )

    with transaction.atomic():
        locked = (
            SubscriptionCycle.objects.select_for_update()
            .select_related("entitlement")
            .get(pk=cycle.pk)
        )
        if locked.refund_amount_eur is not None and locked.refund_amount_eur != amount:
            raise PaymentGatewayError(_("A refund retry must use the originally requested amount."))
        if locked.refund_status == RefundStatus.REFUNDED:
            return locked
        if locked.refund_status == RefundStatus.PENDING and locked.provider_refund_id:
            return locked
        if locked.refund_status not in {
            RefundStatus.NOT_REQUESTED,
            RefundStatus.PENDING,
            RefundStatus.FAILED,
        }:
            raise PaymentGatewayError(_("This billing cycle cannot be refunded."))
        locked.refund_status = RefundStatus.PENDING
        locked.refund_amount_eur = amount
        locked.refund_reference = locked.refund_reference or (
            f"activity-refund-{locked.pk}-{uuid4().hex[:12]}"
        )
        locked.refund_error = ""
        locked.save(
            update_fields=(
                "refund_status",
                "refund_amount_eur",
                "refund_reference",
                "refund_error",
            )
        )

    try:
        response = client.create_refund(
            locked.provider_capture_id,
            amount=amount,
            reference=locked.refund_reference,
        )
    except PaymentGatewayError as exc:
        locked.refund_status = RefundStatus.FAILED
        locked.refund_error = str(exc)
        locked.save(update_fields=("refund_status", "refund_error"))
        raise

    locked.provider_refund_id = str(response.get("id") or "")
    if not locked.provider_refund_id:
        locked.refund_status = RefundStatus.FAILED
        locked.refund_error = "Easypay returned an incomplete refund response."
        locked.save(update_fields=("provider_refund_id", "refund_status", "refund_error"))
        raise PaymentGatewayError(_("Easypay returned an incomplete refund response."))
    locked.save(update_fields=("provider_refund_id",))
    _record_event(
        locked.entitlement,
        action=SubscriptionManagementAction.REFUND_REQUESTED,
        actor=actor,
        from_status=locked.entitlement.status,
        reason=reason,
        details={"cycle_id": locked.pk, "amount_eur": str(amount)},
    )
    return locked


def confirm_subscription_cycle_refund(cycle, *, provider_refund_id, client=None):
    client = client or EasypayClient()
    refund = client.get_refund(provider_refund_id)
    provider_status = str(refund.get("status") or "").lower()
    if str(refund.get("transaction_key") or "") != cycle.refund_reference:
        raise PaymentGatewayError("The refund reference does not match.")
    try:
        refunded_amount = Decimal(str(refund.get("value"))).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError) as exc:
        raise PaymentGatewayError("The refund amount is invalid.") from exc
    if refunded_amount != cycle.refund_amount_eur:
        raise PaymentGatewayError("The refund amount does not match.")
    if provider_status in {"pending", "waiting"}:
        return cycle
    if provider_status != "success":
        SubscriptionCycle.objects.filter(pk=cycle.pk).update(
            refund_status=RefundStatus.FAILED,
            refund_error="Easypay reported a failed refund.",
        )
        cycle.refresh_from_db(fields=("refund_status", "refund_error"))
        return cycle

    with transaction.atomic():
        locked = SubscriptionCycle.objects.select_for_update().get(pk=cycle.pk)
        locked.refund_status = RefundStatus.REFUNDED
        locked.provider_refund_id = provider_refund_id
        locked.refunded_at = locked.refunded_at or timezone.now()
        locked.refund_error = ""
        locked.save(
            update_fields=(
                "refund_status",
                "provider_refund_id",
                "refunded_at",
                "refund_error",
            )
        )
    issue_activity_credit_note(locked)
    return locked


def reconcile_subscription_cycle_refund(cycle, *, client=None):
    """Recover a confirmed refund when the final provider webhook is missing."""
    cycle = SubscriptionCycle.objects.get(pk=cycle.pk)
    if cycle.refund_status != RefundStatus.PENDING:
        return cycle
    if not cycle.provider_refund_id:
        raise PaymentGatewayError(_("The provider refund identifier is unavailable."))
    return confirm_subscription_cycle_refund(
        cycle,
        provider_refund_id=cycle.provider_refund_id,
        client=client,
    )


def retry_subscription_documents(entitlement, *, actor, reason):
    """Retry only missing documents; TOConline external references keep this idempotent."""
    entitlement = ServiceEntitlement.objects.select_related("offering").get(pk=entitlement.pk)
    issued_count = 0
    if (
        entitlement.offering_mode_snapshot == OfferingMode.PACK
        and not entitlement.toconline_sale_document_id
        and entitlement.paid_at is not None
        and bool(entitlement.provider_capture_id)
    ):
        issue_activity_document(entitlement)
        issued_count += 1
    for cycle in entitlement.subscription_cycles.filter(
        status="paid",
        toconline_sale_document_id="",
        provider_capture_id__gt="",
    ):
        issue_activity_document(entitlement, cycle=cycle)
        issued_count += 1
    _record_event(
        entitlement,
        action=SubscriptionManagementAction.DOCUMENT_RETRIED,
        actor=actor,
        from_status=entitlement.status,
        reason=reason,
        details={"issued_count": issued_count},
    )
    return issued_count


def _locked_subscription(entitlement):
    locked = (
        ServiceEntitlement.objects.select_for_update()
        .select_related("offering")
        .get(pk=entitlement.pk)
    )
    if locked.offering_mode_snapshot != OfferingMode.SUBSCRIPTION:
        raise PaymentGatewayError(_("Only subscriptions can be managed this way."))
    return locked


def _stop_provider_subscription(entitlement, client):
    if entitlement.provider_subscription_id:
        client.stop_subscription(entitlement.provider_subscription_id)


def _release_unpaid_promotion(entitlement):
    """Return a reserved code when no subscription payment was captured."""
    from activities.services.discounts import activity_purchase_reference
    from discounts.services import release_redemption

    release_redemption(activity_purchase_reference(entitlement))


def _record_event(
    entitlement,
    *,
    action,
    actor,
    from_status,
    reason,
    details=None,
):
    SubscriptionManagementEvent.objects.create(
        entitlement=entitlement,
        action=action,
        actor=actor,
        from_status=from_status,
        to_status=entitlement.status,
        reason=reason,
        details=details or {},
    )
