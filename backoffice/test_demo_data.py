from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings

from accounts.models import CustomerProfile
from activities.models import (
    ActivityReservation,
    ServiceEntitlement,
    SessionSeries,
    SubscriptionCycle,
    WeeklyUsageAdjustment,
)
from backoffice.services.demo_data import create_demo_data
from catalog.models import AddOnAvailability, CatalogOffer, CatalogType
from hotel.models import Booking, CatalogItem
from pets.models import Pet

SAFE_DEMO_SETTINGS = {
    "DEBUG": True,
    "DEMO_DATA_ALLOWED": True,
    "DEMO_DATA_PASSWORD": "test-demo-password",
    "EASYPAY_ACCOUNT_ID": "",
    "EASYPAY_API_KEY": "",
    "EASYPAY_WEBHOOK_USERNAME": "",
    "EASYPAY_WEBHOOK_PASSWORD": "",
    "TOCONLINE_OAUTH_CLIENT_ID": "",
    "TOCONLINE_OAUTH_CLIENT_SECRET": "",
}


class DemoEnvironmentSafetyTests(TestCase):
    @override_settings(DEBUG=True, DEMO_DATA_ALLOWED=False)
    def test_requires_explicit_demo_flag(self):
        with self.assertRaisesMessage(
            ImproperlyConfigured,
            "DEMO_DATA_ALLOWED=True",
        ):
            create_demo_data()

    @override_settings(DEBUG=False, DEMO_DATA_ALLOWED=True)
    def test_refuses_non_debug_environment(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "requires DEBUG=True"):
            create_demo_data()

    @override_settings(
        DEBUG=True,
        DEMO_DATA_ALLOWED=True,
        EASYPAY_API_KEY="configured-provider-secret",
    )
    def test_refuses_active_external_credentials(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "EASYPAY_API_KEY"):
            create_demo_data()


@override_settings(**SAFE_DEMO_SETTINGS)
class DemoDataTests(TestCase):
    def test_seed_is_idempotent_and_keeps_unrelated_users(self):
        real_user = get_user_model().objects.create_user(
            username="existing-local-user",
            password="unchanged-password",
        )

        first_summary = create_demo_data()
        second_summary = create_demo_data()

        self.assertEqual(first_summary, second_summary)
        self.assertEqual(first_summary.users, 4)
        self.assertEqual(first_summary.customer_profiles, 2)
        self.assertEqual(first_summary.dogs, 3)
        self.assertEqual(first_summary.catalog_types, 4)
        self.assertEqual(first_summary.offers, 9)
        self.assertEqual(first_summary.entitlements, 4)
        self.assertEqual(first_summary.session_series, 2)
        self.assertEqual(first_summary.reservations, 2)
        self.assertEqual(first_summary.hotel_bookings, 2)
        self.assertTrue(get_user_model().objects.filter(pk=real_user.pk).exists())

        self.assertEqual(
            CustomerProfile.objects.filter(user__username__startswith="demo_").count(),
            2,
        )
        self.assertEqual(
            Pet.objects.filter(owner__user__username__startswith="demo_").count(),
            3,
        )
        self.assertEqual(
            CatalogType.objects.filter(slug__startswith="demo-").count(),
            4,
        )
        self.assertEqual(
            CatalogOffer.objects.filter(catalog_type__slug__startswith="demo-").count(),
            9,
        )
        self.assertEqual(
            SessionSeries.objects.filter(catalog_type__slug__startswith="demo-").count(),
            2,
        )
        self.assertEqual(
            ActivityReservation.objects.filter(
                session__catalog_type__slug__startswith="demo-"
            ).count(),
            2,
        )
        self.assertEqual(
            AddOnAvailability.objects.filter(target_type__slug__startswith="demo-").count(),
            4,
        )
        self.assertEqual(
            WeeklyUsageAdjustment.objects.filter(
                entitlement__merchant_reference__startswith="DEMO-ENT-"
            ).count(),
            1,
        )

    def test_accounts_and_breeds_cover_customer_scenarios(self):
        create_demo_data()

        admin = get_user_model().objects.get(username="demo_admin")
        manager = get_user_model().objects.get(username="demo_manager")
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.is_staff)
        self.assertTrue(manager.is_staff)
        self.assertFalse(manager.is_superuser)
        self.assertTrue(admin.check_password(settings.DEMO_DATA_PASSWORD))
        self.assertSetEqual(
            set(
                Pet.objects.filter(owner__user__username__startswith="demo_").values_list(
                    "breed", flat=True
                )
            ),
            {"Labrador Retriever", "Sem raça definida", "Beagle"},
        )

    def test_demo_records_have_no_live_provider_identifiers(self):
        create_demo_data()

        self.assertFalse(
            CatalogItem.objects.filter(
                item_code__startswith="DEMO-S-",
                toconline_id__isnull=False,
            ).exists()
        )
        entitlements = ServiceEntitlement.objects.filter(merchant_reference__startswith="DEMO-ENT-")
        for entitlement in entitlements:
            self.assertEqual(entitlement.provider_link_id, "")
            self.assertEqual(entitlement.provider_subscription_id, "")
            self.assertEqual(entitlement.provider_payment_id, "")
            self.assertEqual(entitlement.provider_capture_id, "")
            self.assertEqual(entitlement.toconline_sale_document_id, "")

        cycle = SubscriptionCycle.objects.get(provider_cycle_id="DEMO-CYCLE-TRAIN-TROVAO")
        self.assertEqual(cycle.provider_capture_id, "")
        self.assertEqual(cycle.toconline_sale_document_id, "")
        self.assertFalse(
            Booking.objects.filter(notes__startswith="DEMO-BOOKING-")
            .exclude(toconline_sale_document_id="")
            .exists()
        )
