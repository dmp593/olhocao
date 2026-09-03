from django.contrib import admin

from .models import AddOnAvailability, CatalogLevel, CatalogOffer, CatalogType


@admin.register(CatalogType)
class CatalogTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "workflow", "default_capacity", "is_active", "order")
    list_filter = ("workflow", "is_active", "closes_on_national_holidays")
    search_fields = ("name", "slug")


@admin.register(CatalogLevel)
class CatalogLevelAdmin(admin.ModelAdmin):
    list_display = ("name", "catalog_type", "capacity_override", "is_active", "order")
    list_filter = ("catalog_type", "is_active")
    search_fields = ("name", "catalog_type__name")


@admin.register(CatalogOffer)
class CatalogOfferAdmin(admin.ModelAdmin):
    list_display = ("name", "catalog_type", "level", "is_standalone", "is_public", "order")
    list_filter = ("catalog_type", "is_standalone", "is_public", "is_active")
    search_fields = ("name", "catalog_item__name", "catalog_item__item_code")


@admin.register(AddOnAvailability)
class AddOnAvailabilityAdmin(admin.ModelAdmin):
    list_display = ("add_on", "target_type", "target_level", "is_active")
    list_filter = ("target_type", "is_active")
