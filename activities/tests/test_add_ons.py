from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from activities.models import (
    CreditLedgerEntry,
    EntitlementStatus,
    LedgerReason,
    OfferingMode,
    ServiceFulfilmentStatus,
)
from activities.services.add_ons import (
    ActivityAddOnError,
    available_activity_add_ons,
    validate_activity_add_on_purchase,
)
from activities.services.billing import create_entitlement
from activities.services.reservations import reserve_session
from activities.tests.factories import create_offering, create_session
from catalog.models import AddOnAvailability, CatalogWorkflow
from discounts.models import DiscountType, Promotion, SaleChannel
from hotel.services.payments import PaymentGatewayError, PaymentLinkResult
from hotel.tests.factories import create_account, create_pet


class FakePaymentClient:
    def create_single_payment_link(self, **kwargs):
        return PaymentLinkResult(
            provider_link_id="activity-add-on-link",
            url="https://pay.example/activity-add-on",
            qr_code_url="",
            expires_at=kwargs["expires_at"],
        )


class ActivityAddOnTests(TestCase):
    def setUp(self):
        self.customer = create_account(username="add-on-customer")
        self.pet = create_pet(self.customer)
        self.activity_offering = create_offering(remote_id="daycare-plan")
        self.activity_entitlement = self.activity_offering.entitlements.create(
            customer=self.customer,
            pet=self.pet,
            status=EntitlementStatus.ACTIVE,
            item_name_snapshot=self.activity_offering.catalog_item.name,
            unit_price_eur_snapshot=self.activity_offering.price_eur,
            merchant_reference="daycare-entitlement",
            current_period_end=timezone.now() + timedelta(days=30),
        )
        CreditLedgerEntry.objects.create(
            entitlement=self.activity_entitlement,
            delta=5,
            reason=LedgerReason.PURCHASE,
            idempotency_key="daycare-entitlement-credits",
        )
        self.session = create_session(catalog_type=self.activity_offering.catalog_type)
        self.reservation = reserve_session(
            entitlement=self.activity_entitlement,
            session=self.session,
            actor=self.customer.user,
        )
        self.add_on = create_offering(
            mode=OfferingMode.ONE_OFF,
            workflow=CatalogWorkflow.FLEXIBLE,
            remote_id="grooming-add-on",
        )
        AddOnAvailability.objects.create(
            add_on=self.add_on.catalog_offer,
            target_type=self.session.catalog_type,
        )

    def test_available_add_ons_use_catalog_relationship_not_commercial_name(self):
        self.session.catalog_type.name = "A completely renamed service"
        self.session.catalog_type.save(update_fields=("name", "updated_at"))

        offerings = available_activity_add_ons(self.reservation)

        self.assertQuerySetEqual(offerings, [self.add_on])

    def test_paid_add_on_keeps_activity_origin_and_requested_date(self):
        entitlement = create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=self.add_on,
            source_activity_reservation=self.reservation,
            schedule_preference="date",
            preferred_date=timezone.localtime(self.session.starts_at).date(),
            actor=self.customer.user,
            client=FakePaymentClient(),
        )

        fulfilment = entitlement.fulfilments.get()
        self.assertEqual(entitlement.source_activity_reservation, self.reservation)
        self.assertEqual(fulfilment.status, ServiceFulfilmentStatus.AWAITING_PAYMENT)
        self.assertEqual(
            fulfilment.preferred_date,
            timezone.localtime(self.session.starts_at).date(),
        )

    def test_add_on_promotion_uses_host_service_and_reserved_service_date(self):
        service_date = timezone.localtime(self.session.starts_at).date()
        promotion = Promotion.objects.create(
            code="ACTIVITY-EXTRA",
            name="Activity optional service",
            discount_type=DiscountType.PERCENTAGE,
            value=Decimal("10"),
            eligible_sale_channels=[SaleChannel.ADD_ON],
            service_starts_on=service_date,
            service_ends_on=service_date,
        )
        promotion.catalog_types.add(self.add_on.catalog_type)
        promotion.host_catalog_types.add(self.session.catalog_type)

        entitlement = create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=self.add_on,
            source_activity_reservation=self.reservation,
            actor=self.customer.user,
            client=FakePaymentClient(),
            promotion_code=promotion.code,
        )

        self.assertEqual(entitlement.discount_amount_eur_snapshot, Decimal("4.99"))
        self.assertEqual(entitlement.total_price_eur, Decimal("44.91"))

    def test_add_on_promotion_for_another_host_service_is_rejected(self):
        other_host = self.activity_offering.catalog_type.__class__.objects.create(
            name="Other host",
            slug="other-activity-add-on-host",
            workflow=CatalogWorkflow.FULL_DAY_SLOT,
            default_capacity=5,
        )
        promotion = Promotion.objects.create(
            code="WRONG-HOST",
            name="Wrong host",
            discount_type=DiscountType.PERCENTAGE,
            value=Decimal("10"),
            eligible_sale_channels=[SaleChannel.ADD_ON],
        )
        promotion.catalog_types.add(self.add_on.catalog_type)
        promotion.host_catalog_types.add(other_host)

        with self.assertRaisesMessage(PaymentGatewayError, "does not apply"):
            create_entitlement(
                customer=self.customer,
                pet=self.pet,
                offering=self.add_on,
                source_activity_reservation=self.reservation,
                actor=self.customer.user,
                client=FakePaymentClient(),
                promotion_code=promotion.code,
            )

        self.assertFalse(self.add_on.entitlements.exists())

    def test_same_open_add_on_cannot_be_attached_twice(self):
        self.add_on.entitlements.create(
            customer=self.customer,
            pet=self.pet,
            source_activity_reservation=self.reservation,
            status=EntitlementStatus.ACTIVE,
            item_name_snapshot=self.add_on.catalog_item.name,
            unit_price_eur_snapshot=self.add_on.price_eur,
            merchant_reference="existing-activity-add-on",
        )

        with self.assertRaisesMessage(ActivityAddOnError, "already attached"):
            validate_activity_add_on_purchase(
                reservation=self.reservation,
                offering=self.add_on,
                customer=self.customer,
            )

    def test_customer_can_open_add_on_checkout_for_owned_reservation(self):
        self.client.force_login(self.customer.user)

        list_response = self.client.get(
            reverse("activities:reservation_add_ons", args=(self.reservation.pk,))
        )
        purchase_response = self.client.get(
            reverse(
                "activities:purchase_reservation_add_on",
                args=(self.reservation.pk, self.add_on.pk),
            )
        )

        self.assertContains(list_response, self.add_on.catalog_offer.name)
        self.assertContains(purchase_response, self.pet.name)
        self.assertContains(
            purchase_response,
            timezone.localtime(self.session.starts_at).strftime("%d/%m/%Y"),
        )

    def test_customer_cannot_open_another_customers_add_on_checkout(self):
        other_customer = create_account(username="other-add-on-customer")
        self.client.force_login(other_customer.user)

        response = self.client.get(
            reverse(
                "activities:purchase_reservation_add_on",
                args=(self.reservation.pk, self.add_on.pk),
            )
        )

        self.assertEqual(response.status_code, 404)
