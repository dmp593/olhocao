from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from catalog.models import CatalogOffer, CatalogType, CatalogWorkflow
from discounts.models import (
    DiscountType,
    Promotion,
    PromotionRedemption,
    PurchaseKind,
    RedemptionStatus,
    SaleChannel,
)
from discounts.services import (
    DiscountLine,
    PromotionUnavailable,
    allocate_discount,
    consume_redemption,
    quote_promotion,
    release_redemption,
    reserve_promotion,
)
from hotel.tests.factories import create_account, create_catalog_item, create_pet


class PromotionPricingTests(TestCase):
    def setUp(self):
        self.customer = create_account()
        self.pet = create_pet(self.customer)
        self.catalog_item = create_catalog_item()
        self.catalog_type = CatalogType.objects.create(
            name="Accommodation",
            slug="promotion-accommodation",
            workflow=CatalogWorkflow.DATE_RANGE,
            default_capacity=10,
        )
        self.catalog_offer = CatalogOffer.objects.create(
            name="Hotel stay",
            catalog_type=self.catalog_type,
            catalog_item=self.catalog_item,
            is_active=True,
            is_public=True,
        )

    def promotion(self, **overrides):
        values = {
            "code": "WELCOME",
            "name": "Welcome offer",
            "discount_type": DiscountType.PERCENTAGE,
            "value": Decimal("10"),
            "eligible_sale_channels": [SaleChannel.DATE_RANGE],
        }
        values.update(overrides)
        promotion = Promotion(**values)
        promotion.full_clean()
        promotion.save()
        return promotion

    def line(self, **overrides):
        values = {
            "key": "stay",
            "amount_eur": Decimal("30.00"),
            "sale_channel": SaleChannel.DATE_RANGE,
            "catalog_type_id": self.catalog_type.pk,
            "catalog_level_id": None,
            "catalog_offer_id": self.catalog_offer.pk,
            "pet_id": self.pet.pk,
            "breed": self.pet.breed,
            "service_start": timezone.localdate() + timedelta(days=10),
            "service_end": timezone.localdate() + timedelta(days=13),
        }
        values.update(overrides)
        return DiscountLine(**values)

    def test_percentage_quote_is_exact_and_case_insensitive(self):
        promotion = self.promotion(value=Decimal("12.5"))

        quote = quote_promotion(
            "  welcome ",
            customer=self.customer,
            lines=(self.line(),),
        )

        self.assertEqual(promotion.code, "WELCOME")
        self.assertEqual(quote.discount_amount_eur, Decimal("3.75"))
        self.assertEqual(quote.total_amount_eur, Decimal("26.25"))
        self.assertEqual(quote.allocations, {"stay": Decimal("3.75")})

    def test_fixed_discount_never_reduces_total_below_zero(self):
        self.promotion(discount_type=DiscountType.FIXED, value=Decimal("50"))

        quote = quote_promotion(
            "WELCOME",
            customer=self.customer,
            lines=(self.line(amount_eur=Decimal("12.00")),),
        )

        self.assertEqual(quote.discount_amount_eur, Decimal("12.00"))
        self.assertEqual(quote.total_amount_eur, Decimal("0.00"))

    def test_allocation_distributes_every_cent_deterministically(self):
        lines = tuple(
            self.line(key=f"line-{index}", amount_eur=Decimal("0.01")) for index in range(3)
        )

        allocations = allocate_discount(lines, Decimal("0.02"))

        self.assertEqual(sum(allocations.values()), Decimal("0.02"))
        self.assertEqual(
            allocations,
            {
                "line-0": Decimal("0.01"),
                "line-1": Decimal("0.01"),
                "line-2": Decimal("0.00"),
            },
        )

    def test_customer_catalog_pet_breed_and_channel_restrictions_are_combined(self):
        promotion = self.promotion(
            eligible_sale_channels=[SaleChannel.DATE_RANGE, SaleChannel.ADD_ON],
            eligible_breeds=[" MIXED "],
        )
        promotion.customers.add(self.customer)
        promotion.catalog_types.add(self.catalog_type)
        promotion.catalog_offers.add(self.catalog_offer)
        promotion.pets.add(self.pet)

        matching_quote = quote_promotion(
            "WELCOME",
            customer=self.customer,
            lines=(self.line(),),
        )

        self.assertEqual(matching_quote.eligible_amount_eur, Decimal("30.00"))
        with self.assertRaisesMessage(PromotionUnavailable, "selected items"):
            quote_promotion(
                "WELCOME",
                customer=self.customer,
                lines=(self.line(breed="Labrador"),),
            )
        other_customer = create_account(username="other-promotion-customer")
        with self.assertRaisesMessage(PromotionUnavailable, "not available for this customer"):
            quote_promotion(
                "WELCOME",
                customer=other_customer,
                lines=(self.line(),),
            )

    def test_hotel_service_period_uses_checkout_as_an_exclusive_date(self):
        first_night = timezone.localdate() + timedelta(days=10)
        last_night = first_night + timedelta(days=2)
        self.promotion(
            service_starts_on=first_night,
            service_ends_on=last_night,
        )

        quote = quote_promotion(
            "WELCOME",
            customer=self.customer,
            lines=(
                self.line(
                    service_start=first_night,
                    service_end=last_night + timedelta(days=1),
                ),
            ),
        )

        self.assertEqual(quote.discount_amount_eur, Decimal("3.00"))
        with self.assertRaisesMessage(PromotionUnavailable, "selected items"):
            quote_promotion(
                "WELCOME",
                customer=self.customer,
                lines=(
                    self.line(
                        service_start=first_night,
                        service_end=last_night + timedelta(days=2),
                    ),
                ),
            )

    def test_purchase_window_and_minimum_amount_are_enforced(self):
        now = timezone.now()
        self.promotion(
            starts_at=now + timedelta(hours=1),
            minimum_eligible_amount_eur=Decimal("40.00"),
        )

        with self.assertRaisesMessage(PromotionUnavailable, "not available yet"):
            quote_promotion(
                "WELCOME",
                customer=self.customer,
                lines=(self.line(),),
                at=now,
            )
        with self.assertRaisesMessage(PromotionUnavailable, "requires at least"):
            quote_promotion(
                "WELCOME",
                customer=self.customer,
                lines=(self.line(),),
                at=now + timedelta(hours=2),
            )

    def test_percentage_above_one_hundred_is_rejected(self):
        promotion = Promotion(
            code="INVALID",
            name="Invalid percentage",
            discount_type=DiscountType.PERCENTAGE,
            value=Decimal("100.01"),
            eligible_sale_channels=[SaleChannel.DATE_RANGE],
        )

        with self.assertRaises(ValidationError):
            promotion.full_clean()

    def test_codes_are_unique_without_regard_to_case(self):
        self.promotion(code="CaseTest")

        with self.assertRaises(IntegrityError), transaction.atomic():
            Promotion.objects.create(
                code="casetest",
                name="Duplicate",
                discount_type=DiscountType.FIXED,
                value=Decimal("1.00"),
                eligible_sale_channels=[SaleChannel.DATE_RANGE],
            )


class PromotionRedemptionTests(TestCase):
    def setUp(self):
        self.customer = create_account(username="redemption-customer")
        self.pet = create_pet(self.customer)
        self.catalog_item = create_catalog_item(remote_id="redemption-stay")
        self.catalog_type = CatalogType.objects.create(
            name="Accommodation",
            slug="redemption-accommodation",
            workflow=CatalogWorkflow.DATE_RANGE,
            default_capacity=10,
        )
        self.catalog_offer = CatalogOffer.objects.create(
            name="Hotel stay",
            catalog_type=self.catalog_type,
            catalog_item=self.catalog_item,
        )
        self.line = DiscountLine(
            key="stay",
            amount_eur=Decimal("20.00"),
            sale_channel=SaleChannel.DATE_RANGE,
            catalog_type_id=self.catalog_type.pk,
            catalog_level_id=None,
            catalog_offer_id=self.catalog_offer.pk,
            pet_id=self.pet.pk,
            breed=self.pet.breed,
        )
        self.promotion = Promotion.objects.create(
            code="LIMITED",
            name="Limited offer",
            discount_type=DiscountType.FIXED,
            value=Decimal("5.00"),
            eligible_sale_channels=[SaleChannel.DATE_RANGE],
            max_redemptions=1,
            max_redemptions_per_customer=1,
        )

    def reserve(self, reference):
        return reserve_promotion(
            "LIMITED",
            customer=self.customer,
            lines=(self.line,),
            purchase_kind=PurchaseKind.HOTEL_BOOKING,
            purchase_reference=reference,
        )

    def test_reservation_is_idempotent_for_the_same_purchase(self):
        first_quote, first_redemption = self.reserve("hotel-booking:1")
        second_quote, second_redemption = self.reserve("hotel-booking:1")

        self.assertEqual(first_quote.discount_amount_eur, Decimal("5.00"))
        self.assertEqual(second_quote.discount_amount_eur, Decimal("5.00"))
        self.assertEqual(first_redemption, second_redemption)
        self.assertEqual(PromotionRedemption.objects.count(), 1)

    def test_reserved_and_consumed_uses_count_toward_limits(self):
        self.reserve("hotel-booking:1")

        with self.assertRaisesMessage(PromotionUnavailable, "usage limit"):
            self.reserve("hotel-booking:2")

        consume_redemption("hotel-booking:1")
        with self.assertRaisesMessage(PromotionUnavailable, "usage limit"):
            self.reserve("hotel-booking:2")

    def test_releasing_an_unpaid_purchase_returns_the_use(self):
        self.reserve("hotel-booking:1")

        release_redemption("hotel-booking:1")
        _quote, replacement = self.reserve("hotel-booking:2")

        self.assertEqual(replacement.status, RedemptionStatus.RESERVED)
        self.assertEqual(
            PromotionRedemption.objects.filter(status=RedemptionStatus.RELEASED).count(),
            1,
        )
        released = PromotionRedemption.objects.get(status=RedemptionStatus.RELEASED)
        self.assertIsNone(released.active_purchase_reference)
        self.assertEqual(replacement.active_purchase_reference, "hotel-booking:2")

    def test_consumed_use_cannot_be_released(self):
        self.reserve("hotel-booking:1")
        consume_redemption("hotel-booking:1")

        self.assertIsNone(release_redemption("hotel-booking:1"))
        self.assertEqual(
            PromotionRedemption.objects.get(purchase_reference="hotel-booking:1").status,
            RedemptionStatus.CONSUMED,
        )
