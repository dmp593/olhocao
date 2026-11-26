"""Deterministic local demonstration data for manual acceptance testing.

This module is deliberately separate from migrations and production fixtures.
It creates realistic relationships while keeping every external provider
identifier empty, so the dataset cannot be mistaken for captured payments.
"""

from dataclasses import dataclass
from datetime import date, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from accounts.models import CustomerProfile, CustomerSyncStatus
from activities.models import (
    ActivityReservation,
    CreditLedgerEntry,
    EasypayFrequency,
    EntitlementStatus,
    LedgerReason,
    OfferingMode,
    ServiceEntitlement,
    ServiceOffering,
    SessionSeries,
    SubscriptionCycle,
    SubscriptionCycleStatus,
    Trainer,
    WeeklyUsageAdjustment,
)
from activities.services.calendar import materialize_session_series
from activities.services.reservations import reserve_session
from catalog.models import (
    AddOnAvailability,
    CatalogLevel,
    CatalogOffer,
    CatalogType,
    CatalogWorkflow,
)
from hotel.models import (
    Booking,
    BookingDocumentStatus,
    BookingStatus,
    BookingStay,
    CatalogBookingKind,
    CatalogItem,
    CatalogItemOrigin,
    CatalogItemType,
    CatalogSyncStatus,
)
from pets.models import Pet, PetSpecies

DEFAULT_DEMO_PASSWORD = "Demo-OlhoCao-2026!"
DEMO_USERNAME_PREFIX = "demo_"
DEMO_CATALOG_SLUG_PREFIX = "demo-"
DEMO_ITEM_CODE_PREFIX = "DEMO-S-"


@dataclass(frozen=True)
class DemoDataSummary:
    users: int
    customer_profiles: int
    dogs: int
    catalog_types: int
    offers: int
    entitlements: int
    session_series: int
    sessions: int
    reservations: int
    hotel_bookings: int


def validate_demo_environment():
    """Refuse demo data anywhere that resembles a deployed environment."""

    if not settings.DEBUG or not settings.DEMO_DATA_ALLOWED:
        raise ImproperlyConfigured("Demo data requires DEBUG=True and DEMO_DATA_ALLOWED=True.")

    credential_names = (
        "EASYPAY_ACCOUNT_ID",
        "EASYPAY_API_KEY",
        "EASYPAY_WEBHOOK_USERNAME",
        "EASYPAY_WEBHOOK_PASSWORD",
        "TOCONLINE_OAUTH_CLIENT_ID",
        "TOCONLINE_OAUTH_CLIENT_SECRET",
    )
    configured_credentials = [
        name for name in credential_names if str(getattr(settings, name, "")).strip()
    ]
    if configured_credentials:
        names = ", ".join(configured_credentials)
        raise ImproperlyConfigured(
            f"Disable external credentials before creating demo data: {names}."
        )


def _update(instance, **values):
    changed_fields = []
    for field_name, value in values.items():
        if getattr(instance, field_name) != value:
            setattr(instance, field_name, value)
            changed_fields.append(field_name)
    if changed_fields:
        if hasattr(instance, "updated_at"):
            changed_fields.append("updated_at")
        instance.save(update_fields=changed_fields)
    return instance


def _demo_user(*, username, first_name, last_name, email, password, is_staff=False):
    user_model = get_user_model()
    user, _ = user_model.objects.get_or_create(username=username)
    _update(
        user,
        first_name=first_name,
        last_name=last_name,
        email=email,
        is_active=True,
        is_staff=is_staff,
        is_superuser=username == "demo_admin",
    )
    user.set_password(password)
    user.save(update_fields=("password",))
    return user


def _customer(user, *, phone, tax_number):
    profile, _ = CustomerProfile.objects.get_or_create(user=user)
    return _update(
        profile,
        billing_phone=phone,
        tax_number=tax_number,
        toconline_customer_id=None,
        toconline_sync_status=CustomerSyncStatus.PENDING,
        toconline_sync_error="Local demo data: deliberately not synchronized.",
        toconline_sync_attempted_at=None,
        toconline_synced_at=None,
    )


def _dog(owner, *, name, breed, gender, birthdate, **health_details):
    dog_species = PetSpecies.get_dog_species()
    dog, _ = Pet.objects.get_or_create(
        owner=owner,
        name=name,
        defaults={
            "species": dog_species,
            "breed": breed,
            "gender": gender,
            "birthdate": birthdate,
        },
    )
    return _update(
        dog,
        species=dog_species,
        breed=breed,
        gender=gender,
        birthdate=birthdate,
        deleted_at=None,
        **health_details,
    )


def _catalog_type(*, slug, name_pt, name_en, workflow, capacity, order):
    catalog_type, _ = CatalogType.objects.get_or_create(slug=slug)
    return _update(
        catalog_type,
        name=name_pt,
        name_pt=name_pt,
        name_en=name_en,
        description="Dados locais para testes manuais.",
        description_pt="Dados locais para testes manuais.",
        description_en="Local data for manual testing.",
        workflow=workflow,
        default_capacity=capacity,
        default_cancellation_notice_hours=24,
        closes_on_national_holidays=workflow != CatalogWorkflow.DATE_RANGE,
        order=order,
        is_active=True,
    )


def _level(catalog_type, *, slug, name, order):
    level, _ = CatalogLevel.objects.get_or_create(catalog_type=catalog_type, slug=slug)
    return _update(
        level,
        name=name,
        name_pt=name,
        name_en=name,
        description="Nível de demonstração.",
        description_pt="Nível de demonstração.",
        description_en="Demonstration level.",
        order=order,
        is_active=True,
    )


def _offer(
    *,
    code,
    catalog_type,
    level,
    name_pt,
    name_en,
    price,
    order,
    mode=None,
    included_sessions=None,
    sessions_per_week=None,
    validity_days=None,
):
    booking_kind = (
        CatalogBookingKind.STAY
        if catalog_type.workflow == CatalogWorkflow.DATE_RANGE
        else CatalogBookingKind.OTHER
    )
    item, _ = CatalogItem.objects.get_or_create(
        toconline_type=CatalogItemType.SERVICE,
        item_code=code,
        defaults={"name": name_pt, "unit_price_eur": price},
    )
    _update(
        item,
        toconline_id=None,
        name=name_pt,
        description="Artigo exclusivo da base de demonstração local.",
        unit_price_eur=Decimal(price),
        tax_code="NOR",
        currency="EUR",
        is_active=True,
        is_public=True,
        is_configured=True,
        booking_kind=booking_kind,
        origin=CatalogItemOrigin.WEBSITE,
        sync_status=CatalogSyncStatus.PENDING,
        sync_error="Local demo data: deliberately not synchronized.",
    )
    offer, _ = CatalogOffer.objects.get_or_create(
        catalog_item=item,
        defaults={
            "name": name_pt,
            "catalog_type": catalog_type,
            "level": level,
        },
    )
    _update(
        offer,
        name=name_pt,
        name_pt=name_pt,
        name_en=name_en,
        description="Preço ou plano para testes manuais.",
        description_pt="Preço ou plano para testes manuais.",
        description_en="Price or plan for manual testing.",
        catalog_type=catalog_type,
        level=level,
        is_standalone=True,
        is_public=True,
        is_active=True,
        order=order,
    )

    if mode is None:
        return offer, None

    offering, _ = ServiceOffering.objects.get_or_create(
        catalog_offer=offer,
        defaults={"mode": mode},
    )
    _update(
        offering,
        mode=mode,
        included_sessions=included_sessions,
        sessions_per_week=sessions_per_week,
        validity_days=validity_days,
        billing_frequency=(EasypayFrequency.MONTHLY if mode == OfferingMode.SUBSCRIPTION else ""),
        cancellation_notice_hours=24,
        is_published=True,
    )
    offering.full_clean()
    return offer, offering


def _entitlement(*, reference, customer, pet, offering, status, period_days=60):
    now = timezone.now()
    entitlement, _ = ServiceEntitlement.objects.get_or_create(
        merchant_reference=reference,
        defaults={
            "customer": customer,
            "pet": pet,
            "offering": offering,
            "status": status,
            "item_name_snapshot": offering.catalog_item.name,
            "unit_price_eur_snapshot": offering.price_eur,
            "currency_snapshot": offering.catalog_item.currency,
            "current_period_start": now,
            "current_period_end": now + timedelta(days=period_days),
            "paid_at": now if status == EntitlementStatus.ACTIVE else None,
            "document_status": BookingDocumentStatus.NOT_REQUIRED,
        },
    )
    return entitlement


def _monday_after(day):
    return day + timedelta(days=7 - day.weekday())


@transaction.atomic
def create_demo_data(password=None):
    """Create or refresh the stable local demo dataset without deleting records."""

    validate_demo_environment()
    password = password or settings.DEMO_DATA_PASSWORD or DEFAULT_DEMO_PASSWORD

    admin = _demo_user(
        username="demo_admin",
        first_name="Marta",
        last_name="Administração",
        email="admin@demo.example.invalid",
        password=password,
        is_staff=True,
    )
    manager = _demo_user(
        username="demo_manager",
        first_name="Rui",
        last_name="Gestor",
        email="gestor@demo.example.invalid",
        password=password,
        is_staff=True,
    )
    ana_user = _demo_user(
        username="demo_ana",
        first_name="Ana",
        last_name="Martins",
        email="ana@demo.example.invalid",
        password=password,
    )
    bruno_user = _demo_user(
        username="demo_bruno",
        first_name="Bruno",
        last_name="Costa",
        email="bruno@demo.example.invalid",
        password=password,
    )
    ana = _customer(ana_user, phone="910000001", tax_number="245716390")
    bruno = _customer(bruno_user, phone="910000002", tax_number="245716391")

    luna = _dog(
        ana,
        name="Luna",
        breed="Labrador Retriever",
        gender="F",
        birthdate=date(2020, 5, 14),
        weight_kg=Decimal("27.50"),
        is_sterilized=True,
        is_vaccinated=True,
    )
    trovao = _dog(
        ana,
        name="Trovão",
        breed="Sem raça definida",
        gender="M",
        birthdate=date(2018, 11, 2),
        medication="Medicação diária às 20:00.",
        allergies="Sensível a frango.",
        is_vaccinated=True,
    )
    nala = _dog(
        bruno,
        name="Nala",
        breed="Beagle",
        gender="F",
        birthdate=date(2022, 2, 21),
        is_sterilized=True,
        is_vaccinated=True,
    )

    hotel = _catalog_type(
        slug="demo-hotel",
        name_pt="Demonstração · Hotel",
        name_en="Demo · Hotel",
        workflow=CatalogWorkflow.DATE_RANGE,
        capacity=4,
        order=90,
    )
    daycare = _catalog_type(
        slug="demo-atl",
        name_pt="Demonstração · ATL",
        name_en="Demo · Daycare",
        workflow=CatalogWorkflow.FULL_DAY_SLOT,
        capacity=12,
        order=91,
    )
    training = _catalog_type(
        slug="demo-treino",
        name_pt="Demonstração · Treino",
        name_en="Demo · Training",
        workflow=CatalogWorkflow.TIMED_SLOT,
        capacity=8,
        order=92,
    )
    grooming = _catalog_type(
        slug="demo-grooming",
        name_pt="Demonstração · Banhos e grooming",
        name_en="Demo · Bath and grooming",
        workflow=CatalogWorkflow.FLEXIBLE,
        capacity=None,
        order=93,
    )

    indoor = _level(hotel, slug="interior", name="Canil interior", order=0)
    outdoor = _level(hotel, slug="exterior", name="Canil exterior", order=1)
    normal = _level(daycare, slug="normal", name="Normal", order=0)
    premium = _level(daycare, slug="premium", name="Premium", order=1)
    group = _level(training, slug="grupo", name="Grupo", order=0)
    individual = _level(training, slug="individual", name="Individual", order=1)

    indoor_offer, _ = _offer(
        code="DEMO-S-HOTEL-IN",
        catalog_type=hotel,
        level=indoor,
        name_pt="Hotel · Canil interior",
        name_en="Hotel · Indoor kennel",
        price="22.00",
        order=0,
    )
    _offer(
        code="DEMO-S-HOTEL-OUT",
        catalog_type=hotel,
        level=outdoor,
        name_pt="Hotel · Canil exterior",
        name_en="Hotel · Outdoor kennel",
        price="17.50",
        order=1,
    )
    _, daycare_pack = _offer(
        code="DEMO-S-ATL-PACK5",
        catalog_type=daycare,
        level=normal,
        name_pt="ATL · Pack de 5 dias",
        name_en="Daycare · 5-day pack",
        price="60.00",
        order=0,
        mode=OfferingMode.PACK,
        included_sessions=5,
        validity_days=90,
    )
    _, daycare_subscription = _offer(
        code="DEMO-S-ATL-SUB3",
        catalog_type=daycare,
        level=premium,
        name_pt="ATL · 3 dias por semana",
        name_en="Daycare · 3 days per week",
        price="145.00",
        order=1,
        mode=OfferingMode.SUBSCRIPTION,
        sessions_per_week=3,
    )
    _, training_pack = _offer(
        code="DEMO-S-TRAIN-PACK4",
        catalog_type=training,
        level=group,
        name_pt="Treino · Pack de 4 sessões",
        name_en="Training · 4-session pack",
        price="95.00",
        order=0,
        mode=OfferingMode.PACK,
        included_sessions=4,
        validity_days=90,
    )
    _, training_subscription = _offer(
        code="DEMO-S-TRAIN-SUB2",
        catalog_type=training,
        level=group,
        name_pt="Treino · 2 sessões por semana",
        name_en="Training · 2 sessions per week",
        price="175.00",
        order=1,
        mode=OfferingMode.SUBSCRIPTION,
        sessions_per_week=2,
    )
    _offer(
        code="DEMO-S-TRAIN-ONE",
        catalog_type=training,
        level=individual,
        name_pt="Treino · Sessão individual",
        name_en="Training · Individual session",
        price="35.00",
        order=2,
        mode=OfferingMode.ONE_OFF,
    )
    bath_offer, _ = _offer(
        code="DEMO-S-BATH",
        catalog_type=grooming,
        level=None,
        name_pt="Banho",
        name_en="Bath",
        price="18.00",
        order=0,
        mode=OfferingMode.ONE_OFF,
    )
    groom_offer, _ = _offer(
        code="DEMO-S-GROOM",
        catalog_type=grooming,
        level=None,
        name_pt="Banho e tosquia",
        name_en="Bath and grooming",
        price="32.00",
        order=1,
        mode=OfferingMode.ONE_OFF,
    )

    for add_on, target_type in (
        (bath_offer, hotel),
        (groom_offer, hotel),
        (bath_offer, daycare),
        (groom_offer, daycare),
    ):
        relation, _ = AddOnAvailability.objects.get_or_create(
            add_on=add_on,
            target_type=target_type,
            target_level=None,
            defaults={"created_by": admin},
        )
        _update(relation, is_active=True)

    trainer, _ = Trainer.objects.get_or_create(user=manager, defaults={"name": "Rui Gestor"})
    _update(
        trainer,
        name="Rui Gestor",
        email=manager.email,
        phone="910000010",
        biography="Treinador de demonstração para validar agendas e capacidades.",
        is_active=True,
    )

    first_monday = _monday_after(timezone.localdate())
    final_date = first_monday + timedelta(days=41)
    daycare_series, _ = SessionSeries.objects.get_or_create(
        catalog_type=daycare,
        title="ATL diário · demonstração",
        defaults={
            "starts_on": first_monday,
            "ends_on": final_date,
            "weekdays": [1, 2, 3, 4, 5],
            "starts_at_time": time(8, 30),
            "ends_at_time": time(18, 0),
            "capacity_override": 12,
            "created_by": admin,
        },
    )
    _update(
        daycare_series,
        starts_on=first_monday,
        ends_on=final_date,
        weekdays=[1, 2, 3, 4, 5],
        starts_at_time=time(8, 30),
        ends_at_time=time(18, 0),
        capacity_override=12,
        skip_closed_days=True,
        is_active=True,
    )
    daycare_series.eligible_levels.set((normal, premium))
    materialize_session_series(daycare_series)

    training_series, _ = SessionSeries.objects.get_or_create(
        catalog_type=training,
        title="Treino de grupo · demonstração",
        defaults={
            "starts_on": first_monday,
            "ends_on": final_date,
            "weekdays": [2, 4],
            "starts_at_time": time(18, 0),
            "ends_at_time": time(19, 0),
            "capacity_override": 8,
            "created_by": admin,
        },
    )
    _update(
        training_series,
        starts_on=first_monday,
        ends_on=final_date,
        weekdays=[2, 4],
        starts_at_time=time(18, 0),
        ends_at_time=time(19, 0),
        capacity_override=8,
        skip_closed_days=True,
        is_active=True,
    )
    training_series.eligible_levels.set((group,))
    training_series.trainers.set((trainer,))
    materialize_session_series(training_series)

    pack_entitlement = _entitlement(
        reference="DEMO-ENT-ATL-PACK-LUNA",
        customer=ana,
        pet=luna,
        offering=daycare_pack,
        status=EntitlementStatus.ACTIVE,
        period_days=90,
    )
    CreditLedgerEntry.objects.get_or_create(
        idempotency_key="demo-credit-atl-pack-luna",
        defaults={
            "entitlement": pack_entitlement,
            "delta": 5,
            "reason": LedgerReason.PURCHASE,
            "note": "Saldo inicial da demonstração.",
            "created_by": admin,
        },
    )
    daycare_session = daycare.sessions.filter(starts_at__gt=timezone.now()).first()
    if daycare_session:
        reserve_session(
            entitlement=pack_entitlement,
            session=daycare_session,
            actor=ana_user,
            notes="Reserva criada pela preparação da demonstração.",
        )

    training_entitlement = _entitlement(
        reference="DEMO-ENT-TRAIN-SUB-TROVAO",
        customer=ana,
        pet=trovao,
        offering=training_subscription,
        status=EntitlementStatus.ACTIVE,
        period_days=30,
    )
    training_session = training.sessions.filter(starts_at__gt=timezone.now()).first()
    if training_session:
        reserve_session(
            entitlement=training_entitlement,
            session=training_session,
            actor=ana_user,
            notes="Reserva de subscrição para demonstração.",
        )
    WeeklyUsageAdjustment.objects.get_or_create(
        entitlement=training_entitlement,
        week_start=first_monday,
        defaults={
            "extra_uses": 1,
            "reason": "Oferta de aniversário do cão (demonstração).",
            "created_by": admin,
        },
    )
    SubscriptionCycle.objects.get_or_create(
        provider_cycle_id="DEMO-CYCLE-TRAIN-TROVAO",
        defaults={
            "entitlement": training_entitlement,
            "status": SubscriptionCycleStatus.PAID,
            "amount_eur": training_entitlement.total_price_eur,
            "provider_capture_id": "",
            "period_start": training_entitlement.current_period_start,
            "period_end": training_entitlement.current_period_end,
            "document_status": BookingDocumentStatus.NOT_REQUIRED,
        },
    )
    _entitlement(
        reference="DEMO-ENT-ATL-PAST-DUE-NALA",
        customer=bruno,
        pet=nala,
        offering=daycare_subscription,
        status=EntitlementStatus.PAST_DUE,
        period_days=7,
    )
    _entitlement(
        reference="DEMO-ENT-TRAIN-PACK-NALA",
        customer=bruno,
        pet=nala,
        offering=training_pack,
        status=EntitlementStatus.PENDING,
        period_days=90,
    )

    for reference, customer, pet, status, start_offset, nights in (
        ("DEMO-BOOKING-PENDING", ana, luna, BookingStatus.PENDING, 14, 3),
        ("DEMO-BOOKING-GIFT", ana, trovao, BookingStatus.COMPLIMENTARY, 30, 1),
    ):
        booking, _ = Booking.objects.get_or_create(
            notes=reference,
            defaults={
                "customer": customer,
                "created_by": admin,
                "status": status,
                "document_status": BookingDocumentStatus.NOT_REQUIRED,
            },
        )
        BookingStay.objects.get_or_create(
            booking=booking,
            pet=pet,
            defaults={
                "catalog_item": indoor_offer.catalog_item,
                "catalog_offer": indoor_offer,
                "catalog_type_reference": hotel.pk,
                "catalog_type_name_snapshot": hotel.name,
                "catalog_level_reference": indoor.pk,
                "catalog_level_name_snapshot": indoor.name,
                "toconline_item_id": "",
                "toconline_item_type": CatalogItemType.SERVICE,
                "item_name_snapshot": indoor_offer.catalog_item.name,
                "item_code_snapshot": indoor_offer.catalog_item.item_code,
                "tax_code_snapshot": indoor_offer.catalog_item.tax_code,
                "unit_price_eur_snapshot": indoor_offer.catalog_item.unit_price_eur,
                "currency_snapshot": "EUR",
                "start_date": timezone.localdate() + timedelta(days=start_offset),
                "end_date": timezone.localdate() + timedelta(days=start_offset + nights),
            },
        )

    demo_types = CatalogType.objects.filter(slug__startswith=DEMO_CATALOG_SLUG_PREFIX)
    return DemoDataSummary(
        users=get_user_model().objects.filter(username__startswith=DEMO_USERNAME_PREFIX).count(),
        customer_profiles=CustomerProfile.objects.filter(
            user__username__startswith=DEMO_USERNAME_PREFIX
        ).count(),
        dogs=Pet.objects.filter(owner__user__username__startswith=DEMO_USERNAME_PREFIX).count(),
        catalog_types=demo_types.count(),
        offers=CatalogOffer.objects.filter(catalog_type__in=demo_types).count(),
        entitlements=ServiceEntitlement.objects.filter(
            merchant_reference__startswith="DEMO-ENT-"
        ).count(),
        session_series=SessionSeries.objects.filter(catalog_type__in=demo_types).count(),
        sessions=sum(catalog_type.sessions.count() for catalog_type in demo_types),
        reservations=ActivityReservation.objects.filter(
            session__catalog_type__in=demo_types
        ).count(),
        hotel_bookings=Booking.objects.filter(notes__startswith="DEMO-BOOKING-").count(),
    )
