from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from activities.models import EntitlementStatus, OfferingMode, SubscriptionCycle
from activities.services.billing import create_entitlement, process_activity_notification
from activities.services.discounts import activity_purchase_reference, offering_discount_line
from activities.services.subscriptions import terminate_subscription
from activities.tests.factories import create_catalog_type, create_offering
from catalog.models import CatalogWorkflow
from discounts.models import (
    DiscountSource,
    DiscountType,
    Promotion,
    RedemptionStatus,
    SaleChannel,
)
from hotel.services.payments import PaymentGatewayError, PaymentLinkResult, SubscriptionResult
from hotel.tests.factories import create_account, create_pet


class RecordingPurchaseClient:
    def create_single_payment_link(self, **payload):
        self.payload = payload
        return PaymentLinkResult(
            provider_link_id="discount-link",
            url="https://pay.example/discount-pack",
            qr_code_url="",
            expires_at=payload["expires_at"],
        )

    def create_subscription(self, **payload):
        self.payload = payload
        return SubscriptionResult(
            provider_subscription_id="discount-subscription",
            authorisation_url="https://pay.example/discount-subscription",
        )

    def stop_subscription(self, provider_subscription_id):
        self.stopped_subscription_id = provider_subscription_id
        return {"status": "inactive"}


class DiscountedPackNotificationClient:
    def __init__(self, entitlement):
        self.entitlement = entitlement

    def get_payment(self, provider_id):
        return {
            "id": provider_id,
            "key": self.entitlement.merchant_reference,
            "value": str(self.entitlement.total_price_eur),
            "currency": self.entitlement.currency_snapshot,
            "payment_status": "paid",
            "method": {"type": "MBW"},
            "captures": [{"id": "discount-capture", "status": "success"}],
        }


class DiscountedSubscriptionNotificationClient:
    def __init__(self, entitlement):
        self.entitlement = entitlement

    def get_subscription(self, provider_id):
        return {
            "message": {
                "id": provider_id,
                "key": self.entitlement.merchant_reference,
                "value": str(self.entitlement.total_price_eur),
                "currency": self.entitlement.currency_snapshot,
                "method": {"type": "CC", "status": "active"},
                "transactions": [
                    {
                        "id": "discount-cycle-transaction",
                        "status": "success",
                        "capture": {"id": "discount-cycle-capture", "status": "success"},
                    }
                ],
            }
        }


class ActivityPromotionTests(TestCase):
    def setUp(self):
        self.customer = create_account(username="activity-discount-customer")
        self.pet = create_pet(self.customer)

    def create_promotion(self, *, sale_channel, value="10", catalog_type=None):
        promotion = Promotion.objects.create(
            code=f"SAVE{value.replace('.', '')}",
            name="Customer care promotion",
            discount_type=DiscountType.PERCENTAGE,
            value=Decimal(value),
            eligible_sale_channels=[sale_channel],
        )
        if catalog_type:
            promotion.catalog_types.add(catalog_type)
        return promotion

    def test_capability_mapping_does_not_depend_on_the_commercial_name(self):
        offering = create_offering()
        offering.catalog_item.name = "GOLD FREEPASS"
        offering.catalog_item.save(update_fields=("name", "updated_at"))

        sale_channel = offering_discount_line(offering, self.pet).sale_channel

        self.assertEqual(sale_channel, SaleChannel.PACK)

    def test_pack_uses_discounted_amount_and_consumes_code_after_capture(self):
        offering = create_offering()
        promotion = self.create_promotion(sale_channel=SaleChannel.PACK)
        client = RecordingPurchaseClient()

        entitlement = create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=offering,
            actor=self.customer.user,
            client=client,
            promotion_code=promotion.code,
        )

        self.assertEqual(client.payload["amount"], Decimal("44.91"))
        self.assertEqual(entitlement.discount_source, DiscountSource.PROMOTION)
        self.assertEqual(entitlement.promotion_code_snapshot, promotion.code)
        self.assertEqual(entitlement.discount_amount_eur_snapshot, Decimal("4.99"))
        self.assertEqual(promotion.redemptions.get().status, RedemptionStatus.RESERVED)

        with (
            patch("activities.services.billing.issue_activity_document_safely"),
            patch("activities.services.billing._send_activation_email"),
        ):
            process_activity_notification(
                {
                    "id": "discount-pack-payment",
                    "key": entitlement.merchant_reference,
                    "type": "capture",
                    "status": "success",
                },
                client=DiscountedPackNotificationClient(entitlement),
            )

        entitlement.refresh_from_db()
        self.assertEqual(entitlement.status, EntitlementStatus.ACTIVE)
        self.assertEqual(entitlement.credit_balance, 5)
        self.assertEqual(promotion.redemptions.get().status, RedemptionStatus.CONSUMED)

    def test_subscription_uses_the_same_discount_on_every_provider_cycle(self):
        offering = create_offering(mode=OfferingMode.SUBSCRIPTION)
        promotion = self.create_promotion(sale_channel=SaleChannel.SUBSCRIPTION)
        purchase_client = RecordingPurchaseClient()
        entitlement = create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=offering,
            actor=self.customer.user,
            client=purchase_client,
            promotion_code=promotion.code,
        )

        self.assertEqual(purchase_client.payload["amount"], Decimal("44.91"))

        with (
            patch("activities.services.billing.issue_activity_document_safely"),
            patch("activities.services.billing._send_activation_email"),
        ):
            process_activity_notification(
                {
                    "id": entitlement.provider_subscription_id,
                    "key": entitlement.merchant_reference,
                    "type": "subscription_capture",
                    "status": "success",
                },
                client=DiscountedSubscriptionNotificationClient(entitlement),
            )

        cycle = SubscriptionCycle.objects.get()
        self.assertEqual(cycle.amount_eur, Decimal("44.91"))
        self.assertEqual(promotion.redemptions.get().status, RedemptionStatus.CONSUMED)

    def test_incompatible_catalog_type_is_rejected_before_provider_creation(self):
        offering = create_offering()
        other_type = create_catalog_type(
            workflow=CatalogWorkflow.TIMED_SLOT,
            slug="promotion-other-type",
            name="Other scheduled service",
        )
        promotion = self.create_promotion(
            sale_channel=SaleChannel.PACK,
            catalog_type=other_type,
        )
        client = RecordingPurchaseClient()

        with self.assertRaisesMessage(PaymentGatewayError, "does not apply"):
            create_entitlement(
                customer=self.customer,
                pet=self.pet,
                offering=offering,
                actor=self.customer.user,
                client=client,
                promotion_code=promotion.code,
            )

        self.assertFalse(hasattr(client, "payload"))
        self.assertFalse(offering.entitlements.exists())
        self.assertFalse(promotion.redemptions.exists())

    def test_discount_below_provider_minimum_rolls_back_the_purchase_and_reservation(self):
        offering = create_offering()
        promotion = self.create_promotion(sale_channel=SaleChannel.PACK, value="100")

        with self.assertRaisesMessage(PaymentGatewayError, "minimum online payment"):
            create_entitlement(
                customer=self.customer,
                pet=self.pet,
                offering=offering,
                actor=self.customer.user,
                client=RecordingPurchaseClient(),
                promotion_code=promotion.code,
            )

        self.assertFalse(offering.entitlements.exists())
        self.assertFalse(promotion.redemptions.exists())

    def test_terminating_an_unpaid_subscription_releases_the_reserved_use(self):
        offering = create_offering(
            mode=OfferingMode.SUBSCRIPTION,
        )
        promotion = self.create_promotion(sale_channel=SaleChannel.SUBSCRIPTION)
        entitlement = create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=offering,
            actor=self.customer.user,
            client=RecordingPurchaseClient(),
            promotion_code=promotion.code,
        )

        terminate_subscription(
            entitlement,
            actor=self.customer.user,
            reason="Customer abandoned authorisation",
            client=RecordingPurchaseClient(),
        )

        redemption = promotion.redemptions.get(
            purchase_reference=activity_purchase_reference(entitlement)
        )
        self.assertEqual(redemption.status, RedemptionStatus.RELEASED)
        self.assertIsNone(redemption.active_purchase_reference)
