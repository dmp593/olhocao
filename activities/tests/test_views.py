from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from activities.models import (
    CreditLedgerEntry,
    EntitlementStatus,
    LedgerReason,
    OfferingMode,
)
from activities.tests.factories import create_offering, create_session
from hotel.tests.factories import create_account, create_pet


class ActivityViewTests(TestCase):
    def setUp(self):
        self.customer = create_account()
        self.pet = create_pet(self.customer)
        self.offering = create_offering()

    def test_public_offering_page_uses_local_catalog_item(self):
        response = self.client.get(reverse("activities:offering_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.offering.catalog_item.name)
        self.assertContains(response, "49.90")
        self.assertContains(response, "Buy")

    def test_subscription_uses_subscribe_call_to_action(self):
        create_offering(mode=OfferingMode.SUBSCRIPTION, remote_id="subscription-offer")

        response = self.client.get(reverse("activities:offering_list"))

        self.assertContains(response, "Subscribe")

    def test_inactive_catalog_offer_is_not_public_or_purchasable(self):
        self.offering.catalog_offer.is_active = False
        self.offering.catalog_offer.save(update_fields=("is_active",))

        listing = self.client.get(reverse("activities:offering_list"))
        self.client.force_login(self.customer.user)
        checkout = self.client.get(reverse("activities:purchase", args=(self.offering.pk,)))

        self.assertNotContains(listing, self.offering.catalog_item.name)
        self.assertEqual(checkout.status_code, 404)

    def test_anonymous_purchase_returns_to_checkout_after_authentication(self):
        purchase_url = reverse("activities:purchase", args=(self.offering.pk,))

        response = self.client.get(purchase_url)

        self.assertRedirects(
            response,
            f"{reverse('accounts:login')}?next={purchase_url}",
            fetch_redirect_response=False,
        )

    def test_customer_portal_does_not_expose_another_customer_service(self):
        another_customer = create_account(username="other")
        another_pet = create_pet(another_customer)
        self.offering.entitlements.create(
            customer=another_customer,
            pet=another_pet,
            status=EntitlementStatus.ACTIVE,
            item_name_snapshot="Private plan",
            unit_price_eur_snapshot=self.offering.price_eur,
            merchant_reference="activity-private",
        )
        self.client.force_login(self.customer.user)

        response = self.client.get(reverse("activities:portal"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Private plan")

    def test_staff_portal_never_exposes_customer_payment_url(self):
        entitlement = self.offering.entitlements.create(
            customer=self.customer,
            pet=self.pet,
            status=EntitlementStatus.PENDING,
            item_name_snapshot=self.offering.catalog_item.name,
            unit_price_eur_snapshot=self.offering.price_eur,
            merchant_reference="staff-cannot-pay",
            payment_url="https://pay.example/customer-only",
        )
        staff = create_account(username="portal-staff", staff=True)
        self.client.force_login(staff.user)
        session = self.client.session
        session["assisted_customer_id"] = self.customer.pk
        session.save()

        response = self.client.get(reverse("activities:portal"))

        self.assertContains(response, "Payment link sent to customer")
        self.assertNotContains(response, entitlement.payment_url)

    @patch("activities.views.create_entitlement")
    def test_purchase_redirects_only_after_service_creates_provider_link(self, create):
        create.return_value = self.offering.entitlements.create(
            customer=self.customer,
            pet=self.pet,
            item_name_snapshot=self.offering.catalog_item.name,
            unit_price_eur_snapshot=self.offering.price_eur,
            merchant_reference="activity-checkout",
            payment_url="https://pay.example/secure",
        )
        self.client.force_login(self.customer.user)

        response = self.client.post(
            reverse("activities:purchase", args=(self.offering.pk,)),
            {"pet": self.pet.pk},
        )

        self.assertRedirects(
            response,
            "https://pay.example/secure",
            fetch_redirect_response=False,
        )
        create.assert_called_once()

    @patch("activities.views.send_entitlement_payment_link_email")
    @patch("activities.views.create_entitlement")
    def test_staff_emails_activity_payment_link_instead_of_opening_checkout(
        self,
        create,
        send,
    ):
        entitlement = self.offering.entitlements.create(
            customer=self.customer,
            pet=self.pet,
            item_name_snapshot=self.offering.catalog_item.name,
            unit_price_eur_snapshot=self.offering.price_eur,
            merchant_reference="assisted-activity",
            payment_url="https://pay.example/customer-only",
        )
        create.return_value = entitlement
        staff = create_account(username="activity-staff", staff=True)
        self.client.force_login(staff.user)
        session = self.client.session
        session["assisted_customer_id"] = self.customer.pk
        session.save()

        response = self.client.post(
            reverse("activities:purchase", args=(self.offering.pk,)),
            {"pet": self.pet.pk},
        )

        self.assertRedirects(
            response,
            reverse("backoffice:user_detail", args=(self.customer.user_id,)),
        )
        self.assertNotIn("assisted_customer_id", self.client.session)
        send.assert_called_once_with(entitlement)
        self.assertFalse(create.call_args.kwargs["notify_customer"])

    def test_session_reservation_requires_owned_entitlement(self):
        entitlement = self.offering.entitlements.create(
            customer=self.customer,
            pet=self.pet,
            status=EntitlementStatus.ACTIVE,
            item_name_snapshot=self.offering.catalog_item.name,
            unit_price_eur_snapshot=self.offering.price_eur,
            merchant_reference="activity-owned",
            current_period_end=timezone.now() + timedelta(days=30),
        )
        CreditLedgerEntry.objects.create(
            entitlement=entitlement,
            delta=2,
            reason=LedgerReason.PURCHASE,
            idempotency_key="view-credits",
        )
        session = create_session()
        self.client.force_login(self.customer.user)

        response = self.client.post(
            reverse("activities:reserve", args=(session.pk,)),
            {"entitlement": entitlement.pk, "notes": "Calm introduction"},
        )

        self.assertRedirects(response, reverse("activities:portal"))
        self.assertEqual(entitlement.reservations.count(), 1)

    def test_reservation_form_hides_entitlement_outside_session_access_period(self):
        session = create_session(starts_in_days=10)
        entitlement = self.offering.entitlements.create(
            customer=self.customer,
            pet=self.pet,
            status=EntitlementStatus.ACTIVE,
            item_name_snapshot=self.offering.catalog_item.name,
            unit_price_eur_snapshot=self.offering.price_eur,
            merchant_reference="activity-period-not-started",
            current_period_start=session.starts_at + timedelta(days=1),
            current_period_end=session.starts_at + timedelta(days=30),
        )
        CreditLedgerEntry.objects.create(
            entitlement=entitlement,
            delta=2,
            reason=LedgerReason.PURCHASE,
            idempotency_key="future-period-credits",
        )
        self.client.force_login(self.customer.user)

        response = self.client.get(reverse("activities:reserve", args=(session.pk,)))

        self.assertNotContains(response, "activity-period-not-started")
        self.assertNotContains(response, f'value="{entitlement.pk}"')

    def test_reservation_form_hides_pack_without_remaining_credits(self):
        session = create_session()
        entitlement = self.offering.entitlements.create(
            customer=self.customer,
            pet=self.pet,
            status=EntitlementStatus.ACTIVE,
            item_name_snapshot=self.offering.catalog_item.name,
            unit_price_eur_snapshot=self.offering.price_eur,
            merchant_reference="activity-empty-pack",
            current_period_end=timezone.now() + timedelta(days=30),
        )
        self.client.force_login(self.customer.user)

        response = self.client.get(reverse("activities:reserve", args=(session.pk,)))

        self.assertNotContains(response, f'value="{entitlement.pk}"')
