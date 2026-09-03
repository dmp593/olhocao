import calendar
import hashlib
import json
import logging
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from activities.models import (
    SUBSCRIPTION_EXCLUSIVE_STATUSES,
    ActivityPaymentEvent,
    ActivityReservation,
    CreditLedgerEntry,
    EntitlementStatus,
    LedgerReason,
    OfferingMode,
    RefundStatus,
    ServiceEntitlement,
    ServiceOffering,
    SubscriptionCycle,
    SubscriptionCycleStatus,
    SubscriptionManagementAction,
    SubscriptionManagementEvent,
)
from activities.services.invoicing import issue_activity_document
from discounts.models import DiscountSource
from discounts.services import PromotionUnavailable, consume_redemption, release_redemption
from hotel.models import BookingDocumentStatus
from hotel.services.payments import (
    MINIMUM_ONLINE_PAYMENT_EUR,
    EasypayClient,
    PaymentGatewayError,
    normalize_portuguese_phone,
    verified_chargebacks_for_capture,
)
from pets.models import Pet

logger = logging.getLogger(__name__)


def create_entitlement(
    *,
    customer,
    pet,
    offering,
    actor,
    client=None,
    notify_customer=True,
    promotion_code="",
    schedule_preference="unscheduled",
    preferred_date=None,
    preferred_time=None,
    customer_notes="",
    source_activity_reservation=None,
):
    """Create one payment agreement and return its hosted authorisation URL."""
    client = client or EasypayClient()
    offering = ServiceOffering.objects.select_related(
        "catalog_offer__catalog_item",
        "catalog_offer__catalog_type",
        "catalog_offer__level",
    ).get(pk=offering.pk)
    if pet.owner_id != customer.pk:
        raise PaymentGatewayError(_("The selected dog does not belong to this customer."))
    if not offering.is_available:
        raise PaymentGatewayError(_("This service is no longer available."))
    user = customer.user
    if not user.email:
        raise PaymentGatewayError(_("An email address is required for online payment."))

    customer_data = {
        "name": user.get_full_name() or user.username,
        "email": user.email,
        "phone": normalize_portuguese_phone(customer.phone_number),
        "language": "PT",
        "key": f"olhocao-customer-{customer.pk}",
    }
    if customer.tax_number:
        customer_data["fiscal_number"] = customer.tax_number

    try:
        with transaction.atomic():
            # Lock one stable row before checking open purchases. Conditional
            # unique constraints are not enforced by every supported database,
            # while this row lock serializes purchases for the same dog on all
            # production backends.
            pet = Pet.objects.select_for_update().get(pk=pet.pk)
            locked_source_reservation = None
            if source_activity_reservation is not None:
                locked_source_reservation = (
                    ActivityReservation.objects.select_for_update()
                    .select_related(
                        "session__catalog_type",
                        "entitlement__customer__user",
                        "entitlement__offering__catalog_offer__level",
                        "pet",
                    )
                    .get(pk=source_activity_reservation.pk)
                )
            existing = (
                ServiceEntitlement.objects.select_for_update()
                .filter(
                    customer=customer,
                    pet=pet,
                    offering=offering,
                    status=EntitlementStatus.PENDING,
                )
                .first()
            )
            if existing and (
                existing.source_activity_reservation_id
                != getattr(locked_source_reservation, "pk", None)
            ):
                raise PaymentGatewayError(
                    _(
                        "There is already an unfinished payment for this service and dog. "
                        "Complete or let that link expire before starting another purchase."
                    )
                )
            if locked_source_reservation is not None:
                from activities.services.add_ons import (
                    ActivityAddOnError,
                    validate_activity_add_on_purchase,
                )

                try:
                    validate_activity_add_on_purchase(
                        reservation=locked_source_reservation,
                        offering=offering,
                        customer=customer,
                        exclude_entitlement_id=getattr(existing, "pk", None),
                    )
                except ActivityAddOnError as exc:
                    raise PaymentGatewayError(exc.messages[0]) from exc
            if existing and existing.payment_url:
                if (
                    existing.offering.mode in {OfferingMode.ONE_OFF, OfferingMode.PACK}
                    and existing.payment_expires_at
                    and existing.payment_expires_at <= timezone.now()
                ):
                    existing.status = EntitlementStatus.EXPIRED
                    existing.save(update_fields=("status", "updated_at"))
                    from activities.services.discounts import activity_purchase_reference

                    release_redemption(activity_purchase_reference(existing))
                    existing = None
                else:
                    if (
                        promotion_code
                        and existing.promotion_code_snapshot != str(promotion_code).strip().upper()
                    ):
                        raise PaymentGatewayError(
                            _("A different promotion is already attached to this payment link.")
                        )
                    return existing

            if offering.mode == OfferingMode.SUBSCRIPTION:
                conflicting_subscription = (
                    ServiceEntitlement.objects.select_for_update()
                    .filter(
                        customer=customer,
                        pet=pet,
                        catalog_type_reference=offering.catalog_offer.catalog_type_id,
                        offering_mode_snapshot=OfferingMode.SUBSCRIPTION,
                        status__in=SUBSCRIPTION_EXCLUSIVE_STATUSES,
                    )
                    .exclude(pk=getattr(existing, "pk", None))
                    .first()
                )
                if conflicting_subscription:
                    raise PaymentGatewayError(
                        _(
                            "This dog already has an open subscription for this activity. "
                            "Cancel or finish it before choosing another plan."
                        )
                    )

            entitlement = existing or ServiceEntitlement.objects.create(
                customer=customer,
                pet=pet,
                offering=offering,
                source_activity_reservation=locked_source_reservation,
                offering_mode_snapshot=offering.mode,
                included_sessions_snapshot=offering.included_sessions,
                sessions_per_week_snapshot=offering.sessions_per_week,
                validity_days_snapshot=offering.validity_days,
                billing_frequency_snapshot=offering.billing_frequency,
                cancellation_notice_hours_snapshot=offering.cancellation_notice_hours,
                status=EntitlementStatus.PENDING,
                item_name_snapshot=offering.catalog_item.name,
                item_code_snapshot=offering.catalog_item.item_code,
                tax_code_snapshot=offering.catalog_item.tax_code,
                unit_price_eur_snapshot=offering.catalog_item.unit_price_eur,
                currency_snapshot=offering.catalog_item.currency,
                merchant_reference=f"activity-{uuid4().hex}",
                created_by=actor,
            )
            if promotion_code:
                if (
                    entitlement.discount_source == DiscountSource.PROMOTION
                    and entitlement.promotion_code_snapshot != str(promotion_code).strip().upper()
                ):
                    raise PaymentGatewayError(
                        _("A different promotion is already attached to this purchase.")
                    )
                if entitlement.discount_source != DiscountSource.PROMOTION:
                    from activities.services.discounts import apply_promotion_to_entitlement

                    try:
                        apply_promotion_to_entitlement(
                            entitlement,
                            promotion_code,
                            actor=actor,
                        )
                    except PromotionUnavailable as exc:
                        raise PaymentGatewayError(str(exc)) from exc
                    # The pricing service locks and updates its own model instance.
                    # Reload the purchase snapshot before validating or sending the
                    # amount to the payment provider.
                    entitlement.refresh_from_db()
            if entitlement.total_price_eur < MINIMUM_ONLINE_PAYMENT_EUR:
                raise PaymentGatewayError(
                    _(
                        "This promotion would reduce the service below the minimum online "
                        "payment amount. Please contact the team."
                    )
                )
    except IntegrityError as exc:
        raise PaymentGatewayError(
            _(
                "This dog already has an open subscription for this activity. "
                "Cancel or finish it before choosing another plan."
            )
        ) from exc

    from activities.services.fulfilments import create_awaiting_payment_fulfilment

    create_awaiting_payment_fulfilment(
        entitlement,
        schedule_preference=schedule_preference,
        preferred_date=preferred_date,
        preferred_time=preferred_time,
        customer_notes=customer_notes,
        actor=actor,
    )

    try:
        if offering.mode in {OfferingMode.ONE_OFF, OfferingMode.PACK}:
            expires_at = timezone.now() + timedelta(hours=settings.EASYPAY_PAYMENT_LINK_TTL_HOURS)
            result = client.create_single_payment_link(
                amount=entitlement.total_price_eur,
                merchant_reference=entitlement.merchant_reference,
                description=f"Olhocao {entitlement.item_name_snapshot}",
                customer=customer_data,
                expires_at=expires_at,
                notify_customer=notify_customer,
            )
            entitlement.provider_link_id = result.provider_link_id
            entitlement.payment_url = result.url
            entitlement.qr_code_url = result.qr_code_url
            entitlement.payment_expires_at = result.expires_at
        else:
            next_charge_at = add_billing_period(
                timezone.now(), entitlement.billing_frequency_snapshot
            )
            result = client.create_subscription(
                amount=entitlement.total_price_eur,
                merchant_reference=entitlement.merchant_reference,
                description=f"Olhocao {entitlement.item_name_snapshot}",
                frequency=entitlement.billing_frequency_snapshot,
                customer=customer_data,
                next_charge_at=next_charge_at,
            )
            entitlement.provider_subscription_id = result.provider_subscription_id
            entitlement.payment_url = result.authorisation_url
    except PaymentGatewayError as exc:
        entitlement.last_payment_error = str(exc)
        entitlement.save(update_fields=("last_payment_error", "updated_at"))
        raise

    entitlement.last_payment_error = ""
    entitlement.save(
        update_fields=(
            "provider_link_id",
            "provider_subscription_id",
            "payment_url",
            "qr_code_url",
            "payment_expires_at",
            "last_payment_error",
            "updated_at",
        )
    )
    return entitlement


def send_entitlement_payment_link_email(entitlement):
    """Email an activity checkout URL while keeping staff out of checkout."""
    entitlement = ServiceEntitlement.objects.select_related(
        "customer__user",
        "pet",
    ).get(pk=entitlement.pk)
    if not entitlement.payment_url or entitlement.status != EntitlementStatus.PENDING:
        raise PaymentGatewayError(_("This payment link is no longer available."))
    if (
        entitlement.offering_mode_snapshot in {OfferingMode.ONE_OFF, OfferingMode.PACK}
        and entitlement.payment_expires_at
        and entitlement.payment_expires_at <= timezone.now()
    ):
        raise PaymentGatewayError(_("This payment link is no longer available."))

    user = entitlement.customer.user
    if not user.email:
        raise PaymentGatewayError(_("An email address is required to send the payment link."))
    try:
        send_mail(
            _("Complete payment for %(service_name)s")
            % {"service_name": entitlement.item_name_snapshot},
            (
                _(
                    "Hello %(name)s,\n\n"
                    "Use this secure link to complete payment for %(service_name)s "
                    "for %(pet_name)s:\n%(payment_url)s\n\n"
                    "If you did not request this service, contact Olhó Cão."
                )
                % {
                    "name": user.get_full_name() or user.username,
                    "service_name": entitlement.item_name_snapshot,
                    "pet_name": entitlement.pet.name,
                    "payment_url": entitlement.payment_url,
                }
            ),
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            using="default",
        )
    except Exception as exc:
        logger.exception(
            "Could not email activity payment link",
            extra={"entitlement_id": entitlement.pk},
        )
        raise PaymentGatewayError(
            _("The payment link could not be emailed. Please try again.")
        ) from exc


def process_activity_notification(payload, *, client=None):
    client = client or EasypayClient()
    reference = str(payload.get("key") or "")
    event_type = str(payload.get("type") or "").lower()
    fingerprint = _notification_fingerprint(payload)
    if event_type == "refund":
        cycle = (
            SubscriptionCycle.objects.select_related("entitlement")
            .filter(refund_reference=reference)
            .first()
        )
        purchase_refund = None
        if cycle is None:
            from activities.models import EntitlementRefund

            purchase_refund = (
                EntitlementRefund.objects.select_related("entitlement")
                .filter(refund_reference=reference)
                .first()
            )
        if cycle is None and purchase_refund is None:
            raise PaymentGatewayError("Unknown activity refund notification reference.")
        provider_refund_id = str(payload.get("id") or "")
        if not provider_refund_id:
            raise PaymentGatewayError("Activity refund notification has no provider id.")
        if ActivityPaymentEvent.objects.filter(fingerprint=fingerprint).exists():
            return cycle.entitlement if cycle else purchase_refund.entitlement
        if cycle is not None:
            from activities.services.subscriptions import confirm_subscription_cycle_refund

            confirm_subscription_cycle_refund(
                cycle,
                provider_refund_id=provider_refund_id,
                client=client,
            )
            refund_entitlement = cycle.entitlement
        else:
            from activities.services.refunds import confirm_entitlement_refund

            confirm_entitlement_refund(
                purchase_refund,
                provider_refund_id=provider_refund_id,
                client=client,
            )
            refund_entitlement = purchase_refund.entitlement
        ActivityPaymentEvent.objects.create(
            fingerprint=fingerprint,
            entitlement=refund_entitlement,
            event_type=event_type,
            provider_event_id=provider_refund_id,
            provider_status=str(payload.get("status") or ""),
        )
        return refund_entitlement

    entitlement = (
        ServiceEntitlement.objects.select_related(
            "offering__catalog_offer__catalog_item",
            "customer__user",
        )
        .filter(merchant_reference=reference)
        .first()
    )
    if entitlement is None:
        raise PaymentGatewayError("Unknown activity payment notification reference.")

    if event_type == "chargeback":
        return _process_activity_chargeback(
            entitlement,
            payload,
            fingerprint=fingerprint,
            client=client,
        )

    if (
        entitlement.offering_mode_snapshot in {OfferingMode.ONE_OFF, OfferingMode.PACK}
        and event_type == "capture"
    ):
        return _process_pack_capture(entitlement, payload, fingerprint, client)
    if entitlement.offering_mode_snapshot == OfferingMode.SUBSCRIPTION and event_type in {
        "subscription_create",
        "subscription_capture",
    }:
        return _process_subscription_event(entitlement, payload, fingerprint, client)
    raise PaymentGatewayError("The notification type does not match the purchased service.")


def cancel_subscription(entitlement, *, actor=None, reason="Customer request", client=None):
    from activities.services.subscriptions import cancel_subscription_at_period_end

    return cancel_subscription_at_period_end(
        entitlement,
        actor=actor,
        reason=reason,
        client=client,
    )


def _process_pack_capture(entitlement, payload, fingerprint, client):
    provider_payment_id = str(payload.get("id") or "")
    payment = client.get_payment(provider_payment_id)
    if str(payment.get("payment_status") or "").lower() != "paid":
        if str(payload.get("status") or "").lower() in {
            "failed",
            "failure",
            "declined",
            "cancelled",
            "error",
        }:
            _verify_pack_payment_identity(entitlement, payment)
            return _record_failed_pack_payment(
                entitlement,
                payload,
                fingerprint=fingerprint,
            )
        raise PaymentGatewayError("Easypay has not confirmed this pack payment.")
    _verify_pack_payment_identity(entitlement, payment)
    captures = [
        capture
        for capture in payment.get("captures") or []
        if str(capture.get("status") or "").lower() in {"success", "refunded"}
    ]
    if not captures or not captures[0].get("id"):
        raise PaymentGatewayError("Easypay returned no successful pack capture.")

    now = timezone.now()
    with transaction.atomic():
        locked = (
            ServiceEntitlement.objects.select_for_update()
            .select_related("offering")
            .get(pk=entitlement.pk)
        )
        ActivityPaymentEvent.objects.get_or_create(
            fingerprint=fingerprint,
            defaults=_event_defaults(locked, payload),
        )
        locked.status = EntitlementStatus.ACTIVE
        locked.provider_payment_id = provider_payment_id
        locked.provider_capture_id = str(captures[0]["id"])
        locked.payment_method = str((payment.get("method") or {}).get("type") or "").upper()
        locked.paid_at = locked.paid_at or now
        locked.current_period_start = locked.current_period_start or now
        if locked.validity_days_snapshot:
            locked.current_period_end = locked.current_period_end or (
                now + timedelta(days=locked.validity_days_snapshot)
            )
        locked.document_status = BookingDocumentStatus.PENDING
        locked.last_payment_error = ""
        locked.save()
        CreditLedgerEntry.objects.get_or_create(
            idempotency_key=f"pack-purchase-{locked.pk}",
            defaults={
                "entitlement": locked,
                "delta": locked.included_sessions_snapshot or 1,
                "reason": LedgerReason.PURCHASE,
                "note": locked.item_name_snapshot,
            },
        )
    issue_activity_document_safely(locked)
    from activities.services.fulfilments import activate_paid_entitlement_fulfilments

    activate_paid_entitlement_fulfilments(locked)
    from activities.services.discounts import activity_purchase_reference

    consume_redemption(activity_purchase_reference(locked))
    _send_activation_email(locked)
    return locked


def _verify_pack_payment_identity(entitlement, payment):
    if str(payment.get("key") or "") != entitlement.merchant_reference:
        raise PaymentGatewayError("The pack payment reference does not match.")
    if _amount(payment.get("value")) != entitlement.total_price_eur:
        raise PaymentGatewayError("The paid amount does not match the pack price.")
    if str(payment.get("currency") or "EUR").upper() != entitlement.currency_snapshot:
        raise PaymentGatewayError("The payment currency does not match the pack currency.")


def _record_failed_pack_payment(entitlement, payload, *, fingerprint):
    """Record a verified failed checkout without granting pack credits."""
    with transaction.atomic():
        locked = ServiceEntitlement.objects.select_for_update().get(pk=entitlement.pk)
        ActivityPaymentEvent.objects.get_or_create(
            fingerprint=fingerprint,
            defaults=_event_defaults(locked, payload),
        )
        if locked.status == EntitlementStatus.PENDING:
            locked.last_payment_error = _(
                "The payment was not completed. You can safely try again."
            )
            locked.save(update_fields=("last_payment_error", "updated_at"))
    return locked


def _process_activity_chargeback(entitlement, payload, *, fingerprint, client):
    """Block disputed access while preserving the original purchase and receipt."""
    if ActivityPaymentEvent.objects.filter(fingerprint=fingerprint).exists():
        return entitlement

    cycle = None
    if entitlement.offering_mode_snapshot == OfferingMode.SUBSCRIPTION:
        notified_capture_id = str(payload.get("id") or "")
        cycle = entitlement.subscription_cycles.filter(
            provider_capture_id=notified_capture_id,
            status=SubscriptionCycleStatus.PAID,
        ).first()
        if cycle is None:
            raise PaymentGatewayError("Unknown subscription cycle chargeback.")
        capture_id = cycle.provider_capture_id
        maximum_amount = cycle.amount_eur
    else:
        capture_id = entitlement.provider_capture_id
        maximum_amount = entitlement.total_price_eur

    if not capture_id:
        raise PaymentGatewayError("The disputed activity payment has no provider capture id.")
    chargebacks = verified_chargebacks_for_capture(
        client,
        capture_id=capture_id,
        payment_id=entitlement.provider_payment_id,
        maximum_amount=maximum_amount,
    )
    chargeback_ids = [str(chargeback["id"]) for chargeback in chargebacks]
    total_amount = sum(
        (_amount(chargeback.get("amount", chargeback.get("value"))) for chargeback in chargebacks),
        0,
    )

    provider_stop_error = ""
    if entitlement.offering_mode_snapshot == OfferingMode.SUBSCRIPTION:
        if not entitlement.provider_subscription_id:
            provider_stop_error = _(
                "The provider subscription identifier is missing and requires "
                "administrative attention."
            )
        else:
            try:
                client.stop_subscription(entitlement.provider_subscription_id)
            except PaymentGatewayError:
                provider_stop_error = _(
                    "Automatic provider suspension failed and requires administrative attention."
                )
                logger.exception(
                    "Could not stop a disputed activity subscription",
                    extra={"entitlement_id": entitlement.pk},
                )

    with transaction.atomic():
        locked = ServiceEntitlement.objects.select_for_update().get(pk=entitlement.pk)
        _payment_event, created = ActivityPaymentEvent.objects.get_or_create(
            fingerprint=fingerprint,
            defaults={
                "entitlement": locked,
                "event_type": "chargeback",
                "provider_event_id": chargeback_ids[-1],
                "provider_status": str(payload.get("status") or "received"),
                "details": {
                    "chargeback_ids": chargeback_ids,
                    "amount_eur": str(total_amount),
                    "capture_id": capture_id,
                    "requires_fiscal_review": True,
                    "provider_stop_error": str(provider_stop_error),
                },
            },
        )
        if not created:
            return locked

        now = timezone.now()
        previous_status = locked.status
        locked.access_blocked_at = locked.access_blocked_at or now
        locked.access_blocked_reason = _(
            "Access blocked because the related payment is under dispute."
        )
        locked.last_payment_error = _(
            "The payment was disputed. Financial and fiscal review is required."
        )
        update_fields = [
            "access_blocked_at",
            "access_blocked_reason",
            "last_payment_error",
            "updated_at",
        ]
        if cycle is not None:
            locked.status = EntitlementStatus.SUSPENDED
            locked.suspended_at = locked.suspended_at or now
            update_fields.extend(("status", "suspended_at"))
            SubscriptionCycle.objects.filter(pk=cycle.pk).update(
                status=SubscriptionCycleStatus.DISPUTED
            )
        locked.save(update_fields=update_fields)
        SubscriptionManagementEvent.objects.create(
            entitlement=locked,
            action=SubscriptionManagementAction.PAYMENT_DISPUTED,
            actor=None,
            from_status=previous_status,
            to_status=locked.status,
            reason=_("Easypay reported a payment chargeback."),
            details={
                "chargeback_ids": chargeback_ids,
                "amount_eur": str(total_amount),
                "cycle_id": cycle.pk if cycle else None,
                "provider_stop_error": str(provider_stop_error),
            },
        )
    return locked


def _process_subscription_event(entitlement, payload, fingerprint, client):
    response = client.get_subscription(entitlement.provider_subscription_id or payload.get("id"))
    subscription = response.get("message", response) if isinstance(response, dict) else {}
    if str(subscription.get("key") or "") != entitlement.merchant_reference:
        raise PaymentGatewayError("The subscription reference does not match.")
    if _amount(subscription.get("value")) != entitlement.total_price_eur:
        raise PaymentGatewayError("The subscription amount does not match the service price.")
    if str(subscription.get("currency") or "EUR").upper() != entitlement.currency_snapshot:
        raise PaymentGatewayError("The subscription currency does not match the service currency.")

    is_success = str(payload.get("status") or "").lower() == "success"
    event_type = str(payload.get("type") or "").lower()
    # A failed cycle must never inherit the capture id from an earlier paid
    # cycle returned in the subscription transaction history.
    provider_capture_id = _subscription_capture_id(subscription) if is_success else ""
    if event_type == "subscription_capture" and is_success and not provider_capture_id:
        raise PaymentGatewayError("Easypay returned no successful subscription capture.")
    now = timezone.now()
    cycle = None
    cycle_created = False
    should_refund_cycle = False
    with transaction.atomic():
        locked = (
            ServiceEntitlement.objects.select_for_update()
            .select_related("offering")
            .get(pk=entitlement.pk)
        )
        ActivityPaymentEvent.objects.get_or_create(
            fingerprint=fingerprint,
            defaults=_event_defaults(locked, payload),
        )
        if event_type == "subscription_create":
            if is_success:
                locked.payment_method = str(
                    (subscription.get("method") or {}).get("type") or "CC"
                ).upper()
                if locked.status == EntitlementStatus.PENDING and not locked.access_blocked_at:
                    locked.last_payment_error = ""
            elif locked.status == EntitlementStatus.PENDING:
                locked.last_payment_error = _(
                    "The subscription could not be authorised. Please try again safely."
                )
            locked.save(update_fields=("payment_method", "last_payment_error", "updated_at"))
            return locked

        provider_cycle_id = _subscription_cycle_id(
            subscription,
            fingerprint=fingerprint,
            is_success=is_success,
        )
        period_start = now
        period_end = add_billing_period(period_start, locked.billing_frequency_snapshot)
        cycle, cycle_created = SubscriptionCycle.objects.get_or_create(
            provider_cycle_id=provider_cycle_id,
            defaults={
                "entitlement": locked,
                "status": (
                    SubscriptionCycleStatus.PAID if is_success else SubscriptionCycleStatus.FAILED
                ),
                "amount_eur": locked.total_price_eur,
                "provider_capture_id": provider_capture_id,
                "payment_method": str(
                    (subscription.get("method") or {}).get("type") or locked.payment_method
                ).upper(),
                "period_start": period_start,
                "period_end": period_end,
                "document_status": (
                    BookingDocumentStatus.PENDING
                    if is_success
                    else BookingDocumentStatus.NOT_REQUIRED
                ),
            },
        )
        if is_success and cycle_created:
            previous_status = locked.status
            locked.paid_at = locked.paid_at or now
            locked.current_period_start = period_start
            locked.current_period_end = period_end
            if previous_status in {
                EntitlementStatus.PENDING,
                EntitlementStatus.ACTIVE,
                EntitlementStatus.PAST_DUE,
            }:
                locked.status = EntitlementStatus.ACTIVE
                locked.suspended_at = None
                locked.resume_scheduled_at = None
                locked.last_payment_error = ""
            elif (
                previous_status == EntitlementStatus.SUSPENDED
                and locked.resume_scheduled_at
                and now >= locked.resume_scheduled_at
            ):
                locked.status = EntitlementStatus.ACTIVE
                locked.suspended_at = None
                locked.resume_scheduled_at = None
                locked.last_payment_error = ""
            elif previous_status in {
                EntitlementStatus.CANCEL_AT_PERIOD_END,
                EntitlementStatus.CANCELLED,
                EntitlementStatus.EXPIRED,
                EntitlementStatus.SUSPENDED,
            }:
                locked.last_payment_error = (
                    "Unexpected subscription payment received; automatic refund requested."
                )
                should_refund_cycle = True
        elif (
            not is_success
            and cycle_created
            and locked.status
            in {
                EntitlementStatus.PENDING,
                EntitlementStatus.ACTIVE,
                EntitlementStatus.PAST_DUE,
            }
        ):
            has_current_paid_period = bool(
                locked.current_period_end and locked.current_period_end > now
            )
            if not has_current_paid_period:
                locked.status = EntitlementStatus.PAST_DUE
            locked.last_payment_error = "Easypay could not collect this subscription cycle."
        locked.save()
    if is_success:
        from activities.services.discounts import activity_purchase_reference

        consume_redemption(activity_purchase_reference(locked))
        document_id = issue_activity_document_safely(locked, cycle=cycle)
        if document_id:
            _send_cycle_document_email(cycle)
        if should_refund_cycle:
            _request_unexpected_subscription_refund(cycle, client=client)
            _send_unexpected_subscription_payment_email(locked, cycle)
        elif locked.status == EntitlementStatus.ACTIVE:
            _send_activation_email(locked)
    return locked


def add_billing_period(value, frequency):
    if frequency == "1W":
        return value + timedelta(weeks=1)
    if frequency == "2W":
        return value + timedelta(weeks=2)
    months = {"1M": 1, "2M": 2, "3M": 3, "6M": 6, "1Y": 12}.get(frequency)
    if months is None:
        raise PaymentGatewayError("Unsupported subscription billing frequency.")
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def issue_activity_document_safely(entitlement, *, cycle=None):
    try:
        return issue_activity_document(entitlement, cycle=cycle)
    except Exception:
        logger.exception(
            "TOConline document issuance failed after an activity payment",
            extra={"entitlement_id": entitlement.pk, "cycle_id": getattr(cycle, "pk", None)},
        )
        return None


def _send_cycle_document_email(cycle):
    cycle = SubscriptionCycle.objects.select_related(
        "entitlement__customer__user",
        "entitlement__pet",
    ).get(pk=cycle.pk)
    if cycle.document_notification_sent_at or not cycle.toconline_sale_document_id:
        return
    entitlement = cycle.entitlement
    user = entitlement.customer.user
    try:
        send_mail(
            _("Your receipt for %(service_name)s is available")
            % {"service_name": entitlement.item_name_snapshot},
            _(
                "Hello %(name)s,\n\nYour receipt for %(service_name)s for %(pet_name)s "
                "is available in your customer area:\n%(documents_url)s"
            )
            % {
                "name": user.get_full_name() or user.username,
                "service_name": entitlement.item_name_snapshot,
                "pet_name": entitlement.pet.name,
                "documents_url": f"{settings.PUBLIC_BASE_URL}/activities/my/documents/",
            },
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            using="default",
        )
    except Exception:
        logger.exception(
            "Could not send activity document notification",
            extra={"entitlement_id": entitlement.pk, "cycle_id": cycle.pk},
        )
        return
    cycle.document_notification_sent_at = timezone.now()
    cycle.save(update_fields=("document_notification_sent_at",))


def _send_activation_email(entitlement):
    entitlement.refresh_from_db(fields=("confirmation_sent_at",))
    if entitlement.confirmation_sent_at:
        return
    user = entitlement.customer.user
    try:
        send_mail(
            f"{entitlement.item_name_snapshot} is ready",
            (
                f"Hello {user.get_full_name() or user.username},\n\n"
                f"Your service for {entitlement.pet.name} is active. "
                f"Manage reservations at {settings.PUBLIC_BASE_URL}/activities/my/."
            ),
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            using="default",
        )
    except Exception:
        logger.exception(
            "Could not send activity activation email",
            extra={"entitlement_id": entitlement.pk},
        )
        return
    entitlement.confirmation_sent_at = timezone.now()
    entitlement.save(update_fields=("confirmation_sent_at", "updated_at"))


def _event_defaults(entitlement, payload):
    return {
        "entitlement": entitlement,
        "event_type": str(payload.get("type") or ""),
        "provider_event_id": str(payload.get("id") or ""),
        "provider_status": str(payload.get("status") or ""),
    }


def _subscription_capture_id(subscription):
    """Return the latest successful capture exposed by the Easypay subscription detail."""
    transactions = subscription.get("transactions") or []
    for transaction_data in reversed(transactions):
        capture = transaction_data.get("capture") or transaction_data
        if str(capture.get("status") or "").lower() in {"success", "paid"}:
            return str(capture.get("id") or "")
    return ""


def _subscription_cycle_id(subscription, *, fingerprint, is_success):
    """Build a stable cycle identity from Easypay's transaction data.

    Notification timestamps can differ between deliveries, so the webhook
    fingerprint is only a fallback when the provider exposes no transaction id.
    """
    transactions = subscription.get("transactions") or []
    for transaction_data in reversed(transactions):
        capture = transaction_data.get("capture") or {}
        capture_status = str(capture.get("status") or "").lower()
        capture_id = str(capture.get("id") or "")
        if is_success and capture_id and capture_status in {"success", "paid", "refunded"}:
            return f"capture:{capture_id}"

        transaction_id = str(transaction_data.get("id") or "")
        transaction_status = str(transaction_data.get("status") or "").lower()
        if (
            not is_success
            and transaction_id
            and transaction_status in {"failed", "failure", "declined", "error", "cancelled"}
        ):
            return f"transaction:{transaction_id}"
    return fingerprint


def _request_unexpected_subscription_refund(cycle, *, client):
    from activities.services.subscriptions import request_subscription_cycle_refund

    try:
        request_subscription_cycle_refund(
            cycle,
            amount=cycle.amount_eur,
            actor=None,
            reason="Automatic refund for an unexpected subscription payment",
            client=client,
        )
    except PaymentGatewayError:
        logger.exception(
            "Could not request an automatic refund for an unexpected subscription payment",
            extra={"entitlement_id": cycle.entitlement_id, "cycle_id": cycle.pk},
        )


def _send_unexpected_subscription_payment_email(entitlement, cycle):
    cycle.refresh_from_db(fields=("refund_status",))
    user = entitlement.customer.user
    if cycle.refund_status == RefundStatus.FAILED:
        refund_message = _(
            "We detected the unexpected charge and our team will retry the refund. "
            "No action is required from you."
        )
    else:
        refund_message = _("An automatic refund was requested. No action is required from you.")
    try:
        send_mail(
            _("Unexpected payment for %(service_name)s")
            % {"service_name": entitlement.item_name_snapshot},
            _(
                "Hello %(name)s,\n\n"
                "We received a payment for %(service_name)s after the subscription "
                "was no longer accepting renewals. %(refund_message)s\n\n"
                "You can review your documents at %(documents_url)s"
            )
            % {
                "name": user.get_full_name() or user.username,
                "service_name": entitlement.item_name_snapshot,
                "refund_message": refund_message,
                "documents_url": f"{settings.PUBLIC_BASE_URL}/activities/my/documents/",
            },
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            using="default",
        )
    except Exception:
        logger.exception(
            "Could not send unexpected subscription payment email",
            extra={"entitlement_id": entitlement.pk, "cycle_id": cycle.pk},
        )


def _notification_fingerprint(payload):
    serialized = json.dumps(
        {
            "id": payload.get("id"),
            "key": payload.get("key"),
            "type": payload.get("type"),
            "status": payload.get("status"),
            "date": payload.get("date"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def _amount(value):
    from decimal import Decimal, InvalidOperation

    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError) as exc:
        raise PaymentGatewayError("The activity payment amount is invalid.") from exc
