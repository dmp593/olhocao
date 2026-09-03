"""Create deterministic data for the isolated Playwright database."""

# Django must be initialized before importing models used by this standalone script.
# ruff: noqa: E402

import os
from datetime import timedelta

if os.environ.get("E2E_TESTING") != "True":
    raise RuntimeError("E2E seed data may only be created by the isolated E2E server.")

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "olhocao.settings")

import django

django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from activities.models import (
    CreditLedgerEntry,
    EntitlementStatus,
    LedgerReason,
    OfferingMode,
    SubscriptionCycle,
    SubscriptionCycleStatus,
)
from activities.services.reservations import reserve_session
from activities.tests.factories import (
    create_catalog_level,
    create_catalog_type,
    create_offering,
    create_session,
)
from catalog.models import CatalogWorkflow
from hotel.models import (
    BookingStatus,
    CatalogBookingKind,
    CatalogItemOrigin,
    CatalogSyncStatus,
    PaymentAttempt,
)
from hotel.tests.factories import (
    add_booking_extra,
    create_account,
    create_booking,
    create_catalog_item,
    create_pet,
)


def seed_users():
    user_model = get_user_model()
    administrator = user_model.objects.create_superuser(
        username="e2e-admin",
        email="admin@example.com",
        password="e2e-admin-password",
    )
    customer = create_account(username="e2e-customer")
    customer.user.first_name = "Joana"
    customer.user.last_name = "Silva"
    customer.user.save(update_fields=("first_name", "last_name"))
    dog = create_pet(customer, name="Bolinhas")
    operator = user_model.objects.create_user(
        username="e2e-operator",
        email="operator@example.com",
        password="e2e-operator-password",
        is_staff=True,
    )
    return administrator, operator, customer, dog


def seed_hotel(customer, dog):
    accommodation = create_catalog_item(
        remote_id="e2e-accommodation",
        name="Canil jardim",
        price="25.00",
        booking_kind=CatalogBookingKind.STAY,
    )
    accommodation.origin = CatalogItemOrigin.WEBSITE
    accommodation.sync_status = CatalogSyncStatus.PENDING
    accommodation.is_public = True
    accommodation.is_configured = True
    accommodation.save()

    booking, stay = create_booking(
        customer,
        duration=1,
        stay_item=accommodation,
        pet=dog,
    )
    add_booking_extra(stay, price="6.50", quantity=1)
    PaymentAttempt.objects.create(
        booking=booking,
        merchant_reference="e2e-pending-booking-payment",
        # Provider cancellation is exercised with an injected client in the
        # service tests.  The browser fixture deliberately has no remote
        # identifier so an isolated E2E run never calls Easypay.
        provider_link_id="",
        payment_url="https://payments.example.test/e2e-link",
        amount_eur=booking.total_price_eur,
        expires_at=timezone.now() + timedelta(days=1),
    )

    cancelled_booking, _ = create_booking(
        customer,
        status=BookingStatus.CANCELLED,
        start_in_days=30,
        duration=2,
        stay_item=accommodation,
        pet=dog,
    )
    PaymentAttempt.objects.create(
        booking=cancelled_booking,
        merchant_reference="e2e-cancelled-stale-payment",
        provider_link_id="e2e-cancelled-link",
        payment_url="https://payments.example.test/stale-link",
        amount_eur=cancelled_booking.total_price_eur,
        expires_at=timezone.now() + timedelta(days=1),
    )
    return booking


def seed_activities(customer, dog):
    pack = create_offering(remote_id="e2e-daycare-pack")
    pack.catalog_type.name = "ATL E2E"
    pack.catalog_type.name_pt = "ATL E2E"
    pack.catalog_type.name_en = "E2E daycare"
    pack.catalog_type.save(update_fields=("name", "name_pt", "name_en", "updated_at"))
    second_level = create_catalog_level(
        pack.catalog_type,
        name="Premium E2E",
        slug="premium-e2e-ordering",
    )
    second_level.order = 1
    second_level.save(update_fields=("order", "updated_at"))
    pack.catalog_item.name = "ATL cinco sessões"
    pack.catalog_item.is_public = True
    pack.catalog_item.is_configured = True
    pack.catalog_item.save()

    subscription = create_offering(
        mode=OfferingMode.SUBSCRIPTION,
        catalog_type=create_catalog_type(
            workflow=CatalogWorkflow.TIMED_SLOT,
            slug="e2e-training",
            name="Training",
        ),
        remote_id="e2e-training-subscription",
    )
    subscription.catalog_item.name = "Treino duas vezes por semana"
    subscription.catalog_item.is_public = True
    subscription.catalog_item.is_configured = True
    subscription.catalog_item.save()

    entitlement = pack.entitlements.create(
        customer=customer,
        pet=dog,
        status=EntitlementStatus.ACTIVE,
        item_name_snapshot=pack.catalog_item.name,
        unit_price_eur_snapshot=pack.price_eur,
        merchant_reference="e2e-active-pack",
        current_period_start=timezone.now(),
        current_period_end=timezone.now() + timedelta(days=90),
        paid_at=timezone.now(),
    )
    CreditLedgerEntry.objects.create(
        entitlement=entitlement,
        delta=5,
        reason=LedgerReason.PURCHASE,
        idempotency_key="e2e-pack-credit",
    )
    customer_cancelled_session = create_session(
        catalog_type=pack.catalog_type,
        starts_in_days=2,
        capacity=8,
    )
    reserve_session(
        entitlement=entitlement,
        session=customer_cancelled_session,
        actor=customer.user,
    )
    management_cancelled_session = create_session(
        catalog_type=pack.catalog_type,
        starts_in_days=4,
        capacity=8,
    )
    reserve_session(
        entitlement=entitlement,
        session=management_cancelled_session,
        actor=customer.user,
    )
    subscription_entitlement = subscription.entitlements.create(
        customer=customer,
        pet=dog,
        status=EntitlementStatus.ACTIVE,
        item_name_snapshot=subscription.catalog_item.name,
        item_code_snapshot=subscription.catalog_item.item_code,
        tax_code_snapshot=subscription.catalog_item.tax_code,
        unit_price_eur_snapshot=subscription.price_eur,
        currency_snapshot=subscription.catalog_item.currency,
        merchant_reference="e2e-active-subscription",
        provider_subscription_id="e2e-provider-subscription",
        current_period_start=timezone.now(),
        current_period_end=timezone.now() + timedelta(days=30),
        paid_at=timezone.now(),
    )
    SubscriptionCycle.objects.create(
        entitlement=subscription_entitlement,
        provider_cycle_id="e2e-subscription-cycle",
        status=SubscriptionCycleStatus.PAID,
        amount_eur=subscription.price_eur,
        provider_capture_id="e2e-subscription-capture",
        period_start=timezone.now(),
        period_end=timezone.now() + timedelta(days=30),
    )
    create_session(
        catalog_type=subscription.catalog_type,
        starts_in_days=3,
        capacity=6,
    )
    grooming_type = create_catalog_type(
        workflow=CatalogWorkflow.FLEXIBLE,
        slug="e2e-grooming",
        name="Grooming",
    )
    grooming = create_offering(
        mode=OfferingMode.ONE_OFF,
        catalog_type=grooming_type,
        level_name="",
        remote_id="e2e-grooming-one-off",
    )
    grooming.catalog_offer.name = "Banho e grooming"
    grooming.catalog_offer.name_pt = "Banho e grooming"
    grooming.catalog_offer.name_en = "Bath and grooming"
    grooming.catalog_offer.save(update_fields=("name", "name_pt", "name_en", "updated_at"))
    grooming.catalog_item.name = "Banho e grooming"
    grooming.catalog_item.is_public = True
    grooming.catalog_item.is_configured = True
    grooming.catalog_item.save()
    return pack, subscription, grooming


def main():
    if settings.MAILERS["default"]["BACKEND"] != ("django.core.mail.backends.locmem.EmailBackend"):
        raise RuntimeError("E2E tests must use the in-memory email backend.")
    _, _, customer, dog = seed_users()
    seed_hotel(customer, dog)
    seed_activities(customer, dog)


if __name__ == "__main__":
    main()
