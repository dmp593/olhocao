import logging
from decimal import ROUND_DOWN, Decimal

from django.db import transaction

from activities.models import (
    EntitlementRefund,
    OfferingMode,
    RefundStatus,
    ServiceEntitlement,
    SubscriptionCycle,
    SubscriptionCycleStatus,
)
from hotel.models import BookingDocumentStatus, CatalogSyncStatus
from hotel.services.invoicing import PAYMENT_MECHANISM_BY_EASYPAY_METHOD
from olhocao.integrations.toconline import toconline_gateway

logger = logging.getLogger(__name__)


def issue_activity_document(entitlement, *, cycle=None, client=toconline_gateway):
    try:
        with transaction.atomic():
            locked_entitlement = (
                ServiceEntitlement.objects.select_for_update()
                .select_related("customer__user", "offering__catalog_offer__catalog_item")
                .get(pk=entitlement.pk)
            )
            locked_cycle = None
            if cycle is not None:
                locked_cycle = SubscriptionCycle.objects.select_for_update().get(pk=cycle.pk)
                if locked_cycle.toconline_sale_document_id:
                    return locked_cycle.toconline_sale_document_id
                reference = f"OLHOCAO-ACTIVITY-CYCLE-{locked_cycle.pk}"
            else:
                if locked_entitlement.toconline_sale_document_id:
                    return locked_entitlement.toconline_sale_document_id
                reference = f"OLHOCAO-ACTIVITY-PURCHASE-{locked_entitlement.pk}"

            _require_captured_activity_payment(locked_entitlement, cycle=locked_cycle)

            existing = client.find_sales_document(external_reference=reference)
            document = existing or _create_document(
                locked_entitlement,
                reference,
                client,
                amount=(
                    locked_cycle.amount_eur if locked_cycle else locked_entitlement.total_price_eur
                ),
            )
            document_id = str(document["id"])
            _mark_activity_document_issued(
                locked_entitlement,
                document_id,
                cycle=locked_cycle,
            )
            return document_id
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:500]}"
        if cycle is not None:
            SubscriptionCycle.objects.filter(pk=cycle.pk).update(
                document_status=BookingDocumentStatus.FAILED,
                document_error=error,
            )
        else:
            ServiceEntitlement.objects.filter(pk=entitlement.pk).update(
                document_status=BookingDocumentStatus.FAILED,
                document_error=error,
            )
        raise


def _create_document(entitlement, reference, client, *, amount):
    item = entitlement.offering.catalog_item
    user = entitlement.customer.user
    payload = {
        "document_type": "FR",
        "vat_included_prices": True,
        "customer_business_name": user.get_full_name() or user.username,
        "external_reference": reference,
        "payment_mechanism": PAYMENT_MECHANISM_BY_EASYPAY_METHOD.get(
            entitlement.payment_method,
            "CC",
        ),
        "lines": [_activity_document_line(entitlement, item, amount=amount)],
    }
    if entitlement.customer.tax_number:
        payload["customer_tax_registration_number"] = entitlement.customer.tax_number
    if entitlement.customer.toconline_customer_id:
        payload["customer_id"] = entitlement.customer.toconline_customer_id
    return client.create_sales_document(**payload)


def _require_captured_activity_payment(entitlement, *, cycle=None):
    """Keep unpaid or mismatched activity records out of fiscal issuance."""
    if cycle is None:
        if entitlement.offering_mode_snapshot not in {
            OfferingMode.ONE_OFF,
            OfferingMode.PACK,
        }:
            raise ValueError("A subscription document requires a billing cycle.")
        if not entitlement.paid_at or not entitlement.provider_capture_id:
            raise ValueError("The activity purchase has no confirmed payment capture.")
        if entitlement.total_price_eur <= Decimal("0.00"):
            raise ValueError("The activity purchase has no positive paid amount.")
        return

    if cycle.entitlement_id != entitlement.pk:
        raise ValueError("The subscription cycle belongs to a different service agreement.")
    if cycle.status != SubscriptionCycleStatus.PAID or not cycle.provider_capture_id:
        raise ValueError("The subscription cycle has no confirmed payment capture.")
    if cycle.amount_eur != entitlement.total_price_eur:
        raise ValueError("The subscription cycle amount does not match the contract price.")


def _mark_activity_document_issued(entitlement, document_id, *, cycle=None):
    target = cycle or entitlement
    target.toconline_sale_document_id = document_id
    target.document_status = BookingDocumentStatus.ISSUED
    target.document_error = ""
    update_fields = [
        "toconline_sale_document_id",
        "document_status",
        "document_error",
    ]
    if cycle is None:
        update_fields.append("updated_at")
    target.save(update_fields=update_fields)


def issue_activity_credit_note(cycle, *, client=toconline_gateway):
    with transaction.atomic():
        locked_cycle = (
            SubscriptionCycle.objects.select_for_update()
            .select_related(
                "entitlement__customer__user",
                "entitlement__offering__catalog_offer__catalog_item",
            )
            .get(pk=cycle.pk)
        )
        if locked_cycle.toconline_amend_document_id:
            return locked_cycle.toconline_amend_document_id
        if not locked_cycle.toconline_sale_document_id or not locked_cycle.refund_amount_eur:
            raise ValueError("The subscription cycle has no refundable TOConline document.")

        sale_document = client.get_sales_document(locked_cycle.toconline_sale_document_id)
        sale_attributes = sale_document.get("attributes") or sale_document
        parent_reference = sale_attributes.get("document_no")
        if not parent_reference:
            raise ValueError("The TOConline sale document has no document number.")

        entitlement = locked_cycle.entitlement
        customer = entitlement.customer
        user = customer.user
        item = entitlement.offering.catalog_item
        reference = f"OLHOCAO-ACTIVITY-CYCLE-{locked_cycle.pk}-REFUND"
        existing = client.find_sales_document(external_reference=reference)
        if existing:
            document_id = str(existing["id"])
        else:
            payload = {
                "document_type": "NC",
                "parent_document_reference": parent_reference,
                "customer_business_name": user.get_full_name() or user.username,
                "vat_included_prices": True,
                "external_reference": reference,
                "lines": [
                    _activity_document_line(
                        entitlement,
                        item,
                        description=(
                            f"Refund: {entitlement.item_name_snapshot} ({entitlement.pet.name})"
                        ),
                        amount=locked_cycle.refund_amount_eur,
                    )
                ],
                "notes": f"Subscription cycle {locked_cycle.pk} refund",
            }
            if customer.tax_number:
                payload["customer_tax_registration_number"] = customer.tax_number
            document_id = str(client.create_sales_document(**payload)["id"])

        locked_cycle.toconline_amend_document_id = document_id
        locked_cycle.save(update_fields=("toconline_amend_document_id",))
        return document_id


def issue_entitlement_refund_credit_note(refund, *, client=toconline_gateway):
    """Issue one corrective document for a confirmed one-off/pack refund."""

    with transaction.atomic():
        locked_refund = (
            EntitlementRefund.objects.select_for_update()
            .select_related(
                "entitlement__customer__user",
                "entitlement__offering__catalog_offer__catalog_item",
            )
            .get(pk=refund.pk)
        )
        if locked_refund.toconline_amend_document_id:
            return locked_refund.toconline_amend_document_id
        if locked_refund.status != RefundStatus.REFUNDED or not locked_refund.refunded_at:
            raise ValueError("The provider refund has not been confirmed.")

        entitlement = locked_refund.entitlement
        if not entitlement.toconline_sale_document_id:
            raise ValueError("The original activity sale document is unavailable.")
        sale_document = client.get_sales_document(entitlement.toconline_sale_document_id)
        sale_attributes = sale_document.get("attributes") or sale_document
        parent_reference = sale_attributes.get("document_no")
        if not parent_reference:
            raise ValueError("The TOConline sale document has no document number.")

        customer = entitlement.customer
        user = customer.user
        item = entitlement.offering.catalog_item
        reference = f"OLHOCAO-ACTIVITY-PURCHASE-REFUND-{locked_refund.pk}"
        existing = client.find_sales_document(external_reference=reference)
        if existing:
            document_id = str(existing["id"])
        else:
            payload = {
                "document_type": "NC",
                "parent_document_reference": parent_reference,
                "customer_business_name": user.get_full_name() or user.username,
                "vat_included_prices": True,
                "external_reference": reference,
                "lines": [
                    _activity_document_line(
                        entitlement,
                        item,
                        description=(
                            f"Refund: {entitlement.item_name_snapshot} ({entitlement.pet.name})"
                        ),
                        amount=locked_refund.amount_eur,
                    )
                ],
                "notes": f"Activity purchase refund {locked_refund.pk}",
            }
            if customer.tax_number:
                payload["customer_tax_registration_number"] = customer.tax_number
            document_id = str(client.create_sales_document(**payload)["id"])

        locked_refund.toconline_amend_document_id = document_id
        locked_refund.document_error = ""
        locked_refund.save(update_fields=("toconline_amend_document_id", "document_error"))
        return document_id


def _activity_document_line(entitlement, item, *, description=None, amount=None):
    gross_amount = Decimal(str(entitlement.unit_price_eur_snapshot))
    desired_amount = Decimal(str(entitlement.total_price_eur if amount is None else amount))
    if desired_amount <= Decimal("0.00") or desired_amount > gross_amount:
        raise ValueError("The activity document amount is outside the catalog line price.")
    line = {
        "description": description or f"{entitlement.item_name_snapshot} ({entitlement.pet.name})",
        "quantity": 1,
        "unit_price": float(gross_amount),
    }
    if (
        item.toconline_id
        and item.sync_status == CatalogSyncStatus.SYNCED
        and item.remote_deleted_at is None
    ):
        line.update(item_type=item.toconline_type, item_id=item.toconline_id)
    elif entitlement.tax_code_snapshot:
        line["tax_code"] = entitlement.tax_code_snapshot
    if desired_amount != gross_amount:
        discount_percentage = (
            (Decimal("1") - desired_amount / gross_amount) * Decimal("100")
        ).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
        line["settlement_expression"] = format(discount_percentage, "f").rstrip("0").rstrip(".")
    return line
