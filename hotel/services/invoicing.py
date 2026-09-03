import logging
from decimal import ROUND_DOWN, Decimal

from django.db import transaction

from hotel.models import (
    Booking,
    BookingDocumentStatus,
    BookingEvent,
    BookingEventType,
    BookingRefund,
    BookingRefundStatus,
    CatalogSyncStatus,
    PaymentStatus,
)
from olhocao.integrations.toconline import toconline_gateway

logger = logging.getLogger(__name__)


PAYMENT_MECHANISM_BY_EASYPAY_METHOD = {
    "CC": "CC",
    "AP": "CC",
    "GP": "CC",
    "MB": "MB",
    "MBW": "MB",
    "DD": "DDA",
    "VI": "TR",
}


def issue_booking_document(booking, *, client=toconline_gateway):
    try:
        with transaction.atomic():
            locked_booking = (
                Booking.objects.select_for_update()
                .select_related("customer__user")
                .get(pk=booking.pk)
            )
            _require_captured_booking_payment(locked_booking)
            if locked_booking.toconline_sale_document_id:
                _mark_booking_document_issued(
                    locked_booking,
                    locked_booking.toconline_sale_document_id,
                )
                return locked_booking.toconline_sale_document_id

            reference = _sale_reference(locked_booking)
            existing_document = client.find_sales_document(external_reference=reference)
            document = existing_document or client.create_sales_document(
                **_sale_document_payload(locked_booking, reference),
            )
            document_id = str(document["id"])
            _mark_booking_document_issued(locked_booking, document_id)
            return document_id
    except Exception as exc:
        Booking.objects.filter(pk=booking.pk).update(
            document_status=BookingDocumentStatus.FAILED,
            document_error=_safe_error(exc),
        )
        raise


def issue_booking_credit_note(booking, refund_percentage, *, client=toconline_gateway):
    with transaction.atomic():
        locked_booking = (
            Booking.objects.select_for_update().select_related("customer__user").get(pk=booking.pk)
        )
        if locked_booking.toconline_amend_document_id:
            return locked_booking.toconline_amend_document_id
        if not locked_booking.toconline_sale_document_id:
            raise ValueError("The booking has no TOConline sale document.")

        sale_document = client.get_sales_document(locked_booking.toconline_sale_document_id)
        sale_attributes = sale_document.get("attributes") or sale_document
        parent_reference = sale_attributes.get("document_no")
        if not parent_reference:
            raise ValueError("The TOConline sale document has no document number.")

        reference = f"OLHOCAO-BOOKING-{locked_booking.pk}-REFUND"
        existing_document = client.find_sales_document(external_reference=reference)
        document = existing_document or client.create_sales_document(
            document_type="NC",
            parent_document_reference=parent_reference,
            customer_business_name=(
                locked_booking.customer.user.get_full_name()
                or locked_booking.customer.user.username
            ),
            vat_included_prices=True,
            external_reference=reference,
            lines=_booking_lines(locked_booking, multiplier=refund_percentage),
            notes=f"Booking {locked_booking.pk} cancellation",
        )
        document_id = str(document["id"])
        locked_booking.toconline_amend_document_id = document_id
        locked_booking.save(update_fields=("toconline_amend_document_id", "updated_at"))
        return document_id


def issue_booking_refund_credit_note(refund, *, client=toconline_gateway):
    """Issue one corrective document for a confirmed booking refund."""

    with transaction.atomic():
        locked_refund = (
            BookingRefund.objects.select_for_update()
            .select_related("payment_attempt__booking__customer__user")
            .get(pk=refund.pk)
        )
        if locked_refund.toconline_amend_document_id:
            return locked_refund.toconline_amend_document_id
        if locked_refund.status != BookingRefundStatus.REFUNDED or not locked_refund.refunded_at:
            raise ValueError("The provider refund has not been confirmed.")

        payment = locked_refund.payment_attempt
        booking = payment.booking
        if not booking.toconline_sale_document_id:
            raise ValueError("The booking has no TOConline sale document.")
        sale_document = client.get_sales_document(booking.toconline_sale_document_id)
        sale_attributes = sale_document.get("attributes") or sale_document
        parent_reference = sale_attributes.get("document_no")
        if not parent_reference:
            raise ValueError("The TOConline sale document has no document number.")

        reference = f"OLHOCAO-BOOKING-REFUND-{locked_refund.pk}"
        existing_document = client.find_sales_document(external_reference=reference)
        if existing_document:
            document_id = str(existing_document["id"])
        else:
            refund_percentage = locked_refund.amount_eur / payment.amount_eur
            user = booking.customer.user
            payload = {
                "document_type": "NC",
                "parent_document_reference": parent_reference,
                "customer_business_name": user.get_full_name() or user.username,
                "vat_included_prices": True,
                "external_reference": reference,
                "lines": _booking_lines(booking, multiplier=refund_percentage),
                "notes": f"Booking refund {locked_refund.pk}: {locked_refund.reason}",
            }
            if booking.customer.tax_number:
                payload["customer_tax_registration_number"] = booking.customer.tax_number
            document_id = str(client.create_sales_document(**payload)["id"])

        locked_refund.toconline_amend_document_id = document_id
        locked_refund.document_error = ""
        locked_refund.save(update_fields=("toconline_amend_document_id", "document_error"))
        return document_id


def _sale_document_payload(booking, reference):
    user = booking.customer.user
    payload = {
        "document_type": "FR",
        "vat_included_prices": True,
        "customer_business_name": user.get_full_name() or user.username,
        "external_reference": reference,
        "payment_mechanism": _payment_mechanism(booking),
        "lines": _booking_lines(booking),
    }
    if booking.customer.tax_number:
        payload["customer_tax_registration_number"] = booking.customer.tax_number

    if booking.customer.toconline_customer_id:
        payload["customer_id"] = booking.customer.toconline_customer_id

    return payload


def _booking_lines(booking, multiplier=Decimal("1.00")):
    multiplier = Decimal(str(multiplier))
    if not Decimal("0") < multiplier <= Decimal("1"):
        raise ValueError("Document line multiplier must be between 0 and 1.")
    lines = []

    for stay in booking.stays.select_related("pet", "catalog_item").prefetch_related(
        "services__catalog_item"
    ):
        lines.append(
            _line(
                item_type=stay.toconline_item_type,
                item_id=_usable_toconline_item_id(stay.catalog_item),
                tax_code=stay.tax_code_snapshot,
                description=f"{stay.stay_name} ({stay.pet.name})",
                quantity=stay.duration_days,
                unit_price=stay.unit_price_eur,
                discount_amount=stay.discount_amount_eur_snapshot,
                multiplier=multiplier,
            )
        )
        for service in stay.services.all():
            lines.append(
                _line(
                    item_type=service.toconline_item_type,
                    item_id=_usable_toconline_item_id(service.catalog_item),
                    tax_code=service.tax_code_snapshot,
                    description=f"{service.service_name} ({stay.pet.name})",
                    quantity=service.quantity,
                    unit_price=service.unit_price_eur,
                    discount_amount=service.discount_amount_eur_snapshot,
                    multiplier=multiplier,
                )
            )

    if not lines:
        raise ValueError("A booking document requires at least one line.")
    return lines


def _line(
    *,
    item_type,
    item_id,
    tax_code,
    description,
    quantity,
    unit_price,
    multiplier=Decimal("1.00"),
    discount_amount=Decimal("0.00"),
):
    line = {
        "description": description,
        "quantity": quantity,
        "unit_price": float(unit_price),
    }
    if item_type and item_id:
        line.update(item_type=item_type, item_id=item_id)
    elif tax_code:
        line["tax_code"] = tax_code
    gross_amount = Decimal(str(unit_price)) * Decimal(str(quantity))
    discount_amount = Decimal(str(discount_amount))
    if discount_amount < Decimal("0") or discount_amount > gross_amount:
        raise ValueError("Document line discount is outside the gross line amount.")
    desired_amount = (gross_amount - discount_amount) * multiplier
    if desired_amount != gross_amount:
        discount_percentage = (
            (Decimal("1") - desired_amount / gross_amount) * Decimal("100")
            if gross_amount
            else Decimal("0")
        )
        line["settlement_expression"] = (
            format(
                discount_percentage.quantize(Decimal("0.0001"), rounding=ROUND_DOWN),
                "f",
            )
            .rstrip("0")
            .rstrip(".")
        )
    return line


def _usable_toconline_item_id(catalog_item):
    if (
        catalog_item.toconline_id
        and catalog_item.sync_status == CatalogSyncStatus.SYNCED
        and catalog_item.remote_deleted_at is None
    ):
        return catalog_item.toconline_id
    return ""


def _payment_mechanism(booking):
    payment = booking.payment_attempts.filter(
        status__in=(
            PaymentStatus.PAID,
            PaymentStatus.REFUND_PENDING,
            PaymentStatus.REFUNDED,
            PaymentStatus.REFUND_FAILED,
            PaymentStatus.DISPUTED,
        ),
        amount_eur=booking.total_price_eur,
    ).first()
    if not payment:
        return "TR"
    return PAYMENT_MECHANISM_BY_EASYPAY_METHOD.get(payment.payment_method, "TR")


def _require_captured_booking_payment(booking):
    """Reject invoice creation unless the current booking total was captured."""
    if booking.total_price_eur <= Decimal("0.00"):
        raise ValueError("A complimentary booking has no payment document to issue.")
    captured_payment_exists = booking.payment_attempts.filter(
        status__in=(
            PaymentStatus.PAID,
            PaymentStatus.REFUND_PENDING,
            PaymentStatus.REFUNDED,
            PaymentStatus.REFUND_FAILED,
            PaymentStatus.DISPUTED,
        ),
        amount_eur=booking.total_price_eur,
        provider_capture_id__gt="",
    ).exists()
    if not captured_payment_exists:
        raise ValueError("The current booking total has no confirmed payment capture.")


def _mark_booking_document_issued(booking, document_id):
    booking.toconline_sale_document_id = str(document_id)
    booking.document_status = BookingDocumentStatus.ISSUED
    booking.document_error = ""
    booking.save(
        update_fields=(
            "toconline_sale_document_id",
            "document_status",
            "document_error",
            "updated_at",
        )
    )
    BookingEvent.objects.get_or_create(
        booking=booking,
        event_type=BookingEventType.DOCUMENT_ISSUED,
        defaults={"details": {"document_id": str(document_id)}},
    )


def _sale_reference(booking):
    return f"OLHOCAO-BOOKING-{booking.pk}"


def _safe_error(exc):
    return f"{type(exc).__name__}: {str(exc)[:500]}"
