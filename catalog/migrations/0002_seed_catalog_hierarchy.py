from django.db import migrations


def seed_catalog_hierarchy(apps, schema_editor):
    CatalogType = apps.get_model("catalog", "CatalogType")
    CatalogLevel = apps.get_model("catalog", "CatalogLevel")
    CatalogOffer = apps.get_model("catalog", "CatalogOffer")
    CatalogItem = apps.get_model("hotel", "CatalogItem")
    ServiceOffering = apps.get_model("activities", "ServiceOffering")

    catalog_types = {
        "hotel": CatalogType.objects.create(
            name="Hotel",
            name_pt="Hotel",
            name_en="Hotel",
            slug="hotel",
            description="Estadias e alojamento canino.",
            description_pt="Estadias e alojamento canino.",
            description_en="Dog stays and accommodation.",
            workflow="date_range",
            default_capacity=1,
            default_cancellation_notice_hours=168,
            order=0,
        ),
        "daycare": CatalogType.objects.create(
            name="ATL canino",
            name_pt="ATL canino",
            name_en="Dog daycare",
            slug="atl-canino",
            description="Dias de ATL reservados segundo o plano ou pack do cão.",
            description_pt="Dias de ATL reservados segundo o plano ou pack do cão.",
            description_en="Daycare days booked under the dog's plan or pack.",
            workflow="full_day_slot",
            default_capacity=12,
            default_cancellation_notice_hours=24,
            order=1,
        ),
        "training": CatalogType.objects.create(
            name="Treino e obediência",
            name_pt="Treino e obediência",
            name_en="Training and obedience",
            slug="treino-e-obediencia",
            description="Sessões com horário, capacidade e treinadores definidos.",
            description_pt="Sessões com horário, capacidade e treinadores definidos.",
            description_en="Timed sessions with configured capacity and trainers.",
            workflow="timed_slot",
            default_capacity=8,
            default_cancellation_notice_hours=48,
            order=2,
        ),
        "simple": CatalogType.objects.create(
            name="Banhos e grooming",
            name_pt="Banhos e grooming",
            name_en="Baths and grooming",
            slug="banhos-e-grooming",
            description="Serviços vendidos isoladamente ou adicionados a outra reserva.",
            description_pt="Serviços vendidos isoladamente ou adicionados a outra reserva.",
            description_en="Services sold independently or added to another booking.",
            workflow="flexible",
            order=3,
        ),
    }

    for order, item in enumerate(CatalogItem.objects.filter(booking_kind="stay").order_by("id")):
        level = CatalogLevel.objects.create(
            catalog_type=catalog_types["hotel"],
            name=item.name,
            name_pt=item.name,
            name_en=item.name,
            slug=f"alojamento-{item.pk}",
            capacity_override=1,
            order=order,
            is_active=item.is_active,
        )
        CatalogOffer.objects.create(
            catalog_type=catalog_types["hotel"],
            level=level,
            catalog_item=item,
            name=item.name,
            name_pt=item.name,
            name_en=item.name,
            description=item.description,
            description_pt=item.description,
            description_en=item.description,
            image=item.image,
            is_public=item.is_public,
            is_active=item.is_active,
            is_standalone=True,
            order=order,
        )

    level_cache = {}
    for order, offering in enumerate(
        ServiceOffering.objects.select_related("catalog_item").order_by("id")
    ):
        type_key = offering.activity_type
        catalog_type = catalog_types.get(type_key)
        if catalog_type is None:
            continue
        level_key = (type_key, bool(offering.grants_exclusive_access))
        level = level_cache.get(level_key)
        if level is None:
            is_premium = level_key[1]
            level = CatalogLevel.objects.create(
                catalog_type=catalog_type,
                name="Premium" if is_premium else "Normal",
                name_pt="Premium" if is_premium else "Normal",
                name_en="Premium" if is_premium else "Standard",
                slug="premium" if is_premium else "normal",
                order=1 if is_premium else 0,
            )
            level_cache[level_key] = level
        CatalogOffer.objects.create(
            catalog_type=catalog_type,
            level=level,
            catalog_item=offering.catalog_item,
            name=offering.catalog_item.name,
            name_pt=offering.catalog_item.name,
            name_en=offering.catalog_item.name,
            description=offering.catalog_item.description,
            description_pt=offering.catalog_item.description,
            description_en=offering.catalog_item.description,
            image=offering.catalog_item.image,
            is_public=offering.catalog_item.is_public,
            is_active=offering.catalog_item.is_active,
            is_standalone=True,
            order=order,
        )

    extra_items = CatalogItem.objects.filter(booking_kind="extra").order_by("id")
    for order, item in enumerate(extra_items):
        offer = CatalogOffer.objects.create(
            catalog_type=catalog_types["simple"],
            catalog_item=item,
            name=item.name,
            name_pt=item.name,
            name_en=item.name,
            description=item.description,
            description_pt=item.description,
            description_en=item.description,
            image=item.image,
            is_public=item.is_public,
            is_active=item.is_active,
            is_standalone=True,
            order=order,
        )
        apps.get_model("catalog", "AddOnAvailability").objects.create(
            add_on=offer,
            target_type=catalog_types["hotel"],
        )


def remove_seeded_catalog_hierarchy(apps, schema_editor):
    CatalogType = apps.get_model("catalog", "CatalogType")
    seeded_types = CatalogType.objects.filter(
        slug__in=("hotel", "atl-canino", "treino-e-obediencia", "banhos-e-grooming")
    )
    apps.get_model("catalog", "AddOnAvailability").objects.filter(
        target_type__in=seeded_types
    ).delete()
    apps.get_model("catalog", "CatalogOffer").objects.filter(
        catalog_type__in=seeded_types
    ).delete()
    apps.get_model("catalog", "CatalogLevel").objects.filter(
        catalog_type__in=seeded_types
    ).delete()
    seeded_types.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("activities", "0010_serviceentitlement_promotion_and_more"),
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_catalog_hierarchy, remove_seeded_catalog_hierarchy),
    ]
