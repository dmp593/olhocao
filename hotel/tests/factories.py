from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model

from accounts.models import CustomerProfile
from catalog.models import CatalogOffer, CatalogType, CatalogWorkflow
from hotel.models import (
    Booking,
    BookingService,
    BookingStatus,
    BookingStay,
    CatalogBookingKind,
    CatalogItem,
    CatalogItemType,
)
from pets.models import Pet, PetSpecies


def create_account(*, username="customer", staff=False):
    user = get_user_model().objects.create_user(
        username=username,
        password="test-password-123",
        email=f"{username}@example.com",
        first_name="Test",
        last_name="Customer",
        is_staff=staff,
    )
    return CustomerProfile.objects.create(
        user=user,
        billing_phone="912345678",
        tax_number="123456789",
    )


def create_pet(account, *, name="Rex"):
    species, _ = PetSpecies.objects.get_or_create(name="Dog")
    return Pet.objects.create(
        owner=account,
        species=species,
        breed="Mixed",
        gender="M",
        name=name,
        birthdate=date(2020, 1, 1),
    )


def create_catalog_item(
    *,
    remote_id="stay-1",
    name="Hotel",
    price="20.00",
    booking_kind=CatalogBookingKind.STAY,
):
    return CatalogItem.objects.create(
        toconline_id=remote_id,
        toconline_type=CatalogItemType.SERVICE,
        item_code=remote_id.upper(),
        name=name,
        unit_price_eur=Decimal(price),
        tax_code="NOR",
        booking_kind=booking_kind,
    )


def create_catalog_offer(
    item,
    *,
    workflow=None,
    catalog_type=None,
    name=None,
    capacity=10,
):
    workflow = workflow or (
        CatalogWorkflow.DATE_RANGE
        if item.booking_kind == CatalogBookingKind.STAY
        else CatalogWorkflow.FLEXIBLE
    )
    catalog_type = catalog_type or CatalogType.objects.create(
        name=f"Type {item.item_code}",
        slug=f"type-{item.item_code.lower()}",
        workflow=workflow,
        default_capacity=None if workflow == CatalogWorkflow.FLEXIBLE else capacity,
    )
    return CatalogOffer.objects.create(
        name=name or item.name,
        catalog_type=catalog_type,
        catalog_item=item,
        is_active=True,
        is_public=True,
    )


def create_booking(
    account,
    *,
    status=BookingStatus.PENDING,
    start_in_days=14,
    duration=3,
    stay_item=None,
    pet=None,
):
    stay_item = stay_item or create_catalog_item()
    stay_offer = getattr(stay_item, "catalog_offer", None) or create_catalog_offer(stay_item)
    pet = pet or create_pet(account)
    booking = Booking.objects.create(customer=account, status=status)
    stay = BookingStay.objects.create(
        booking=booking,
        pet=pet,
        catalog_item=stay_item,
        catalog_offer=stay_offer,
        catalog_type_reference=stay_offer.catalog_type_id,
        catalog_type_name_snapshot=stay_offer.catalog_type.name,
        catalog_level_reference=stay_offer.level_id,
        catalog_level_name_snapshot="",
        toconline_item_id=stay_item.toconline_id,
        toconline_item_type=stay_item.toconline_type,
        item_name_snapshot=stay_item.name,
        unit_price_eur_snapshot=stay_item.unit_price_eur,
        currency_snapshot=stay_item.currency,
        start_date=date.today() + timedelta(days=start_in_days),
        end_date=date.today() + timedelta(days=start_in_days + duration),
    )
    return booking, stay


def add_booking_extra(stay, *, price="5.00", quantity=2):
    item = create_catalog_item(
        remote_id=f"extra-{CatalogItem.objects.count() + 1}",
        name="Walk",
        price=price,
        booking_kind=CatalogBookingKind.EXTRA,
    )
    offer = create_catalog_offer(item)
    return BookingService.objects.create(
        stay=stay,
        pet=stay.pet,
        catalog_item=item,
        catalog_offer=offer,
        catalog_type_reference=offer.catalog_type_id,
        catalog_type_name_snapshot=offer.catalog_type.name,
        catalog_level_reference=offer.level_id,
        catalog_level_name_snapshot="",
        toconline_item_id=item.toconline_id,
        toconline_item_type=item.toconline_type,
        item_name_snapshot=item.name,
        unit_price_eur_snapshot=item.unit_price_eur,
        currency_snapshot=item.currency,
        quantity=quantity,
    )
