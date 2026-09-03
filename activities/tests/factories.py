from datetime import timedelta

from django.utils import timezone

from activities.models import (
    ActivitySession,
    EasypayFrequency,
    OfferingMode,
    ServiceOffering,
)
from catalog.models import CatalogLevel, CatalogOffer, CatalogType, CatalogWorkflow
from hotel.models import CatalogBookingKind
from hotel.tests.factories import create_catalog_item


def create_catalog_type(
    *,
    workflow=CatalogWorkflow.FULL_DAY_SLOT,
    slug=None,
    name=None,
    capacity=10,
    cancellation_notice_hours=24,
):
    slug = slug or f"test-{workflow}"
    defaults = {
        "name": name or "Dog service",
        "name_pt": name or "Serviço canino",
        "name_en": name or "Dog service",
        "workflow": workflow,
        "default_capacity": None if workflow == CatalogWorkflow.FLEXIBLE else capacity,
        "default_cancellation_notice_hours": cancellation_notice_hours,
        "is_active": True,
    }
    catalog_type, _ = CatalogType.objects.get_or_create(slug=slug, defaults=defaults)
    return catalog_type


def create_catalog_level(catalog_type, *, name="Normal", slug=None):
    slug = slug or name.lower().replace(" ", "-")
    level, _ = CatalogLevel.objects.get_or_create(
        catalog_type=catalog_type,
        slug=slug,
        defaults={
            "name": name,
            "name_pt": name,
            "name_en": name,
        },
    )
    return level


def create_offering(
    *,
    mode=OfferingMode.PACK,
    workflow=CatalogWorkflow.FULL_DAY_SLOT,
    catalog_type=None,
    level_name="Normal",
    remote_id="activity-1",
):
    catalog_type = catalog_type or create_catalog_type(workflow=workflow)
    level = create_catalog_level(catalog_type, name=level_name) if level_name else None
    item = create_catalog_item(
        remote_id=remote_id,
        name=f"{catalog_type.name} offer",
        price="49.90",
        booking_kind=CatalogBookingKind.OTHER,
    )
    catalog_offer = CatalogOffer.objects.create(
        catalog_type=catalog_type,
        level=level,
        catalog_item=item,
        name=item.name,
        name_pt=item.name,
        name_en=item.name,
        description=item.description,
        description_pt=item.description,
        description_en=item.description,
        is_public=True,
        is_active=True,
    )
    values = {
        "catalog_offer": catalog_offer,
        "mode": mode,
        "is_published": True,
    }
    if mode == OfferingMode.ONE_OFF:
        pass
    elif mode == OfferingMode.PACK:
        values.update(included_sessions=5, validity_days=90)
    else:
        values.update(sessions_per_week=2, billing_frequency=EasypayFrequency.MONTHLY)
    offering = ServiceOffering(**values)
    offering.full_clean()
    offering.save()
    return offering


def create_session(
    *,
    workflow=CatalogWorkflow.FULL_DAY_SLOT,
    catalog_type=None,
    eligible_levels=(),
    eligible_level_names=(),
    starts_in_days=3,
    capacity=10,
):
    catalog_type = catalog_type or create_catalog_type(workflow=workflow)
    starts_at = timezone.now() + timedelta(days=starts_in_days)
    session = ActivitySession.objects.create(
        title="Happy dogs",
        catalog_type=catalog_type,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=4),
        capacity=capacity,
    )
    levels = list(eligible_levels)
    levels.extend(create_catalog_level(catalog_type, name=name) for name in eligible_level_names)
    if levels:
        session.eligible_levels.set(levels)
    return session
