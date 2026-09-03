from decimal import Decimal

from django.test import TestCase

from activities.models import (
    EntitlementStatus,
    OfferingMode,
    SubscriptionCycle,
    SubscriptionCycleStatus,
)
from activities.services.invoicing import issue_activity_document
from activities.tests.factories import create_offering
from discounts.models import DiscountSource, DiscountType, Promotion, SaleChannel
from hotel.models import BookingDocumentStatus
from hotel.tests.factories import create_account, create_pet


class RecordingTocOnlineClient:
    def __init__(self):
        self.created = []

    def find_sales_document(self, **filters):
        return None

    def create_sales_document(self, **payload):
        self.created.append(payload)
        return {"id": f"activity-document-{len(self.created)}"}


class ActivityDocumentDiscountTests(TestCase):
    def setUp(self):
        self.customer = create_account(username="activity-invoice-customer")
        self.pet = create_pet(self.customer)

    def discounted_entitlement(self, *, mode):
        offering = create_offering(mode=mode)
        promotion = Promotion.objects.create(
            code="INVOICE10",
            name="Invoice promotion",
            discount_type=DiscountType.PERCENTAGE,
            value=Decimal("10"),
            eligible_sale_channels=[
                SaleChannel.PACK if mode == OfferingMode.PACK else SaleChannel.SUBSCRIPTION
            ],
        )
        return offering.entitlements.create(
            customer=self.customer,
            pet=self.pet,
            status=EntitlementStatus.ACTIVE,
            item_name_snapshot=offering.catalog_item.name,
            unit_price_eur_snapshot=offering.price_eur,
            merchant_reference=f"discounted-{mode}",
            provider_capture_id="captured-payment",
            paid_at=offering.created_at,
            discount_source=DiscountSource.PROMOTION,
            promotion=promotion,
            promotion_code_snapshot=promotion.code,
            promotion_name_snapshot=promotion.name,
            promotion_discount_type_snapshot=promotion.discount_type,
            promotion_value_snapshot=promotion.value,
            discount_amount_eur_snapshot=Decimal("4.99"),
        )

    def test_pack_invoice_preserves_gross_catalog_price_and_applies_discount(self):
        entitlement = self.discounted_entitlement(mode=OfferingMode.PACK)
        client = RecordingTocOnlineClient()

        issue_activity_document(entitlement, client=client)

        entitlement.refresh_from_db()
        line = client.created[0]["lines"][0]
        self.assertEqual(line["unit_price"], 49.9)
        self.assertEqual(line["settlement_expression"], "10")
        self.assertEqual(entitlement.document_status, BookingDocumentStatus.ISSUED)

    def test_subscription_cycle_invoice_uses_the_discounted_recurring_amount(self):
        entitlement = self.discounted_entitlement(mode=OfferingMode.SUBSCRIPTION)
        cycle = SubscriptionCycle.objects.create(
            entitlement=entitlement,
            provider_cycle_id="discounted-cycle",
            status=SubscriptionCycleStatus.PAID,
            amount_eur=Decimal("44.91"),
            provider_capture_id="cycle-capture",
            document_status=BookingDocumentStatus.PENDING,
        )
        client = RecordingTocOnlineClient()

        issue_activity_document(entitlement, cycle=cycle, client=client)

        cycle.refresh_from_db()
        line = client.created[0]["lines"][0]
        self.assertEqual(line["unit_price"], 49.9)
        self.assertEqual(line["settlement_expression"], "10")
        self.assertEqual(cycle.document_status, BookingDocumentStatus.ISSUED)

    def test_unpaid_pack_cannot_generate_a_fiscal_document(self):
        offering = create_offering()
        entitlement = offering.entitlements.create(
            customer=self.customer,
            pet=self.pet,
            status=EntitlementStatus.PENDING,
            item_name_snapshot=offering.catalog_item.name,
            unit_price_eur_snapshot=offering.price_eur,
            merchant_reference="unpaid-pack",
        )

        with self.assertRaisesMessage(ValueError, "no confirmed payment capture"):
            issue_activity_document(entitlement, client=RecordingTocOnlineClient())

        entitlement.refresh_from_db()
        self.assertEqual(entitlement.document_status, BookingDocumentStatus.FAILED)

    def test_cycle_with_a_different_amount_cannot_generate_a_fiscal_document(self):
        entitlement = self.discounted_entitlement(mode=OfferingMode.SUBSCRIPTION)
        cycle = SubscriptionCycle.objects.create(
            entitlement=entitlement,
            provider_cycle_id="wrong-amount-cycle",
            status=SubscriptionCycleStatus.PAID,
            amount_eur=Decimal("49.90"),
            provider_capture_id="wrong-amount-capture",
        )

        with self.assertRaisesMessage(ValueError, "does not match the contract price"):
            issue_activity_document(
                entitlement,
                cycle=cycle,
                client=RecordingTocOnlineClient(),
            )
