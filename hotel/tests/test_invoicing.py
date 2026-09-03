from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from discounts.models import DiscountType, Promotion, SaleChannel
from hotel.models import (
    BookingDocumentStatus,
    BookingRefund,
    BookingRefundStatus,
    BookingStatus,
    CatalogItemOrigin,
    CatalogSyncStatus,
    PaymentAttempt,
    PaymentStatus,
)
from hotel.services.invoicing import (
    issue_booking_credit_note,
    issue_booking_document,
    issue_booking_refund_credit_note,
)
from hotel.services.pricing import apply_promotion_to_booking
from hotel.tests.factories import (
    add_booking_extra,
    create_account,
    create_booking,
    create_catalog_item,
)


class FakeTocOnlineClient:
    def __init__(self, *, existing=None, fail_create=False):
        self.existing = existing
        self.fail_create = fail_create
        self.created = []
        self.retrieved = {
            "id": "sale-1",
            "attributes": {"document_no": "FR 2026/1"},
        }

    def find_sales_document(self, **filters):
        return self.existing

    def create_sales_document(self, **payload):
        if self.fail_create:
            raise RuntimeError("TOConline unavailable")
        self.created.append(payload)
        return {"id": f"document-{len(self.created)}"}

    def get_sales_document(self, document_id):
        return self.retrieved


class BookingDocumentTests(TestCase):
    def setUp(self):
        self.account = create_account()
        self.booking, self.stay = create_booking(
            self.account,
            status=BookingStatus.PAID,
            duration=3,
        )
        add_booking_extra(self.stay, price="5.00", quantity=2)
        PaymentAttempt.objects.create(
            booking=self.booking,
            merchant_reference="paid-booking",
            provider_payment_id="single-1",
            provider_capture_id="capture-1",
            amount_eur=Decimal("70.00"),
            status=PaymentStatus.PAID,
            payment_method="MBW",
        )

    def test_issues_final_invoice_receipt_with_snapshot_lines(self):
        client = FakeTocOnlineClient()

        document_id = issue_booking_document(self.booking, client=client)

        self.booking.refresh_from_db()
        payload = client.created[0]
        self.assertEqual(document_id, "document-1")
        self.assertEqual(payload["document_type"], "FR")
        self.assertTrue(payload["vat_included_prices"])
        self.assertEqual(payload["payment_mechanism"], "MB")
        self.assertEqual(payload["customer_tax_registration_number"], "123456789")
        self.assertEqual(payload["external_reference"], f"OLHOCAO-BOOKING-{self.booking.pk}")
        self.assertEqual(len(payload["lines"]), 2)
        self.assertEqual(payload["lines"][0]["item_id"], "stay-1")
        self.assertEqual(payload["lines"][0]["quantity"], 3)
        self.assertEqual(payload["lines"][0]["unit_price"], 20.0)
        self.assertEqual(payload["lines"][1]["quantity"], 2)
        self.assertEqual(self.booking.document_status, BookingDocumentStatus.ISSUED)

    def test_external_reference_reconciliation_prevents_duplicate_document(self):
        client = FakeTocOnlineClient(existing={"id": "existing-document"})

        first = issue_booking_document(self.booking, client=client)
        second = issue_booking_document(self.booking, client=client)

        self.assertEqual(first, "existing-document")
        self.assertEqual(second, "existing-document")
        self.assertEqual(client.created, [])

    def test_fiscal_failure_does_not_change_paid_booking(self):
        client = FakeTocOnlineClient(fail_create=True)

        with self.assertRaises(RuntimeError):
            issue_booking_document(self.booking, client=client)

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.PAID)
        self.assertEqual(self.booking.document_status, BookingDocumentStatus.FAILED)
        self.assertIn("RuntimeError", self.booking.document_error)

    def test_document_can_be_issued_before_catalog_item_is_published(self):
        item = self.stay.catalog_item
        item.origin = CatalogItemOrigin.WEBSITE
        item.toconline_id = None
        item.sync_status = CatalogSyncStatus.PENDING
        item.save(update_fields=("origin", "toconline_id", "sync_status"))
        self.stay.toconline_item_id = ""
        self.stay.item_code_snapshot = item.item_code
        self.stay.tax_code_snapshot = "NOR"
        self.stay.save(
            update_fields=(
                "toconline_item_id",
                "item_code_snapshot",
                "tax_code_snapshot",
            )
        )
        client = FakeTocOnlineClient()

        issue_booking_document(self.booking, client=client)

        line = client.created[0]["lines"][0]
        self.assertNotIn("item_id", line)
        self.assertEqual(line["tax_code"], "NOR")
        self.assertEqual(line["description"], "Hotel (Rex)")

    def test_unpaid_booking_cannot_generate_a_fiscal_document(self):
        unpaid_booking, _stay = create_booking(
            self.account,
            stay_item=create_catalog_item(remote_id="unpaid-invoice-stay"),
        )

        with self.assertRaisesMessage(ValueError, "no confirmed payment capture"):
            issue_booking_document(unpaid_booking, client=FakeTocOnlineClient())

        unpaid_booking.refresh_from_db()
        self.assertEqual(unpaid_booking.document_status, BookingDocumentStatus.FAILED)

    def test_complimentary_booking_cannot_generate_a_payment_document(self):
        free_booking, _stay = create_booking(
            self.account,
            stay_item=create_catalog_item(remote_id="free-invoice-stay"),
        )
        free_booking.discount_amount_eur_snapshot = free_booking.gross_total_price_eur
        free_booking.status = BookingStatus.COMPLIMENTARY
        free_booking.save(update_fields=("discount_amount_eur_snapshot", "status", "updated_at"))

        with self.assertRaisesMessage(ValueError, "complimentary booking"):
            issue_booking_document(free_booking, client=FakeTocOnlineClient())

    def test_credit_note_uses_original_document_and_percentage_discount(self):
        self.booking.toconline_sale_document_id = "sale-1"
        self.booking.save(update_fields=("toconline_sale_document_id",))
        client = FakeTocOnlineClient()

        document_id = issue_booking_credit_note(
            self.booking,
            Decimal("0.85"),
            client=client,
        )

        self.booking.refresh_from_db()
        payload = client.created[0]
        self.assertEqual(document_id, "document-1")
        self.assertEqual(payload["document_type"], "NC")
        self.assertEqual(payload["parent_document_reference"], "FR 2026/1")
        self.assertEqual(payload["lines"][0]["unit_price"], 20.0)
        self.assertEqual(payload["lines"][0]["settlement_expression"], "15")
        self.assertEqual(self.booking.toconline_amend_document_id, "document-1")

    def test_each_confirmed_partial_refund_gets_its_own_credit_note(self):
        self.booking.toconline_sale_document_id = "sale-1"
        self.booking.save(update_fields=("toconline_sale_document_id",))
        payment = self.booking.payment_attempts.get()
        refund = BookingRefund.objects.create(
            payment_attempt=payment,
            amount_eur=Decimal("35.00"),
            status=BookingRefundStatus.REFUNDED,
            reason="Customer-care adjustment",
            refund_reference="booking-refund-fiscal-document",
            provider_refund_id="provider-refund-fiscal",
            refunded_at=timezone.now(),
        )
        client = FakeTocOnlineClient()

        first = issue_booking_refund_credit_note(refund, client=client)
        second = issue_booking_refund_credit_note(refund, client=client)

        refund.refresh_from_db()
        self.assertEqual(first, "document-1")
        self.assertEqual(second, "document-1")
        self.assertEqual(len(client.created), 1)
        payload = client.created[0]
        self.assertEqual(payload["document_type"], "NC")
        self.assertEqual(payload["parent_document_reference"], "FR 2026/1")
        self.assertEqual(
            payload["external_reference"],
            f"OLHOCAO-BOOKING-REFUND-{refund.pk}",
        )
        self.assertEqual(payload["lines"][0]["settlement_expression"], "50")
        self.assertEqual(refund.toconline_amend_document_id, "document-1")

    def test_invoice_preserves_gross_line_prices_and_applies_promotion_discount(self):
        stay_item = create_catalog_item(remote_id="discounted-invoice-stay")
        booking, stay = create_booking(self.account, duration=2, stay_item=stay_item)
        extra = add_booking_extra(stay, price="5.00", quantity=2)
        promotion = Promotion.objects.create(
            code="INVOICE10",
            name="Invoice discount",
            discount_type=DiscountType.PERCENTAGE,
            value=Decimal("10"),
            eligible_sale_channels=[SaleChannel.DATE_RANGE, SaleChannel.ADD_ON],
        )
        apply_promotion_to_booking(booking, promotion.code, actor=self.account.user)
        booking.status = BookingStatus.PAID
        booking.save(update_fields=("status", "updated_at"))
        PaymentAttempt.objects.create(
            booking=booking,
            merchant_reference="discounted-paid-booking",
            provider_capture_id="discounted-capture",
            amount_eur=Decimal("45.00"),
            status=PaymentStatus.PAID,
            payment_method="CC",
        )
        client = FakeTocOnlineClient()

        issue_booking_document(booking, client=client)

        stay.refresh_from_db()
        extra.refresh_from_db()
        lines = client.created[0]["lines"]
        self.assertEqual(lines[0]["unit_price"], 20.0)
        self.assertEqual(lines[0]["settlement_expression"], "10")
        self.assertEqual(lines[1]["unit_price"], 5.0)
        self.assertEqual(lines[1]["settlement_expression"], "10")
