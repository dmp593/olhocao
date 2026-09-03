from django.contrib import admin

from activities.models import (
    ActivityPaymentEvent,
    ActivityReservation,
    ActivityReservationEvent,
    ActivitySession,
    ActivitySessionEvent,
    CreditLedgerEntry,
    EntitlementRefund,
    OperationalClosure,
    ServiceEntitlement,
    ServiceFulfilment,
    ServiceFulfilmentEvent,
    ServiceOffering,
    SessionSeries,
    SubscriptionCycle,
    SubscriptionManagementEvent,
    Trainer,
    WeeklyUsageAdjustment,
)


class AppendOnlyAdmin(admin.ModelAdmin):
    actions = None

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)


@admin.register(ServiceOffering)
class ServiceOfferingAdmin(admin.ModelAdmin):
    list_display = (
        "catalog_offer",
        "mode",
        "is_published",
    )
    list_filter = ("catalog_offer__catalog_type", "mode", "is_published")
    search_fields = (
        "catalog_offer__catalog_item__name",
        "catalog_offer__catalog_item__item_code",
    )
    autocomplete_fields = ("catalog_offer",)


@admin.register(ActivitySession)
class ActivitySessionAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "catalog_type",
        "starts_at",
        "capacity",
        "is_cancelled",
    )
    list_filter = ("catalog_type", "is_cancelled", "starts_at")
    search_fields = ("title", "notes")


@admin.register(ActivityReservation)
class ActivityReservationAdmin(admin.ModelAdmin):
    list_display = ("pet", "session", "status", "created_at")
    list_filter = ("status", "session__catalog_type", "session__starts_at")
    search_fields = ("pet__name", "pet__owner__user__email", "pet__owner__user__first_name")


@admin.register(ServiceEntitlement)
class ServiceEntitlementAdmin(AppendOnlyAdmin):
    list_display = ("customer", "pet", "offering", "status", "current_period_end")
    list_filter = ("status", "offering__catalog_offer__catalog_type", "offering__mode")
    search_fields = ("customer__user__email", "pet__name", "merchant_reference")


admin.site.register(CreditLedgerEntry, AppendOnlyAdmin)
admin.site.register(EntitlementRefund, AppendOnlyAdmin)
admin.site.register(SubscriptionCycle, AppendOnlyAdmin)
admin.site.register(ActivityPaymentEvent, AppendOnlyAdmin)
admin.site.register(SubscriptionManagementEvent, AppendOnlyAdmin)
admin.site.register(ActivityReservationEvent)
admin.site.register(ActivitySessionEvent, AppendOnlyAdmin)
admin.site.register(Trainer)
admin.site.register(SessionSeries)
admin.site.register(OperationalClosure)
admin.site.register(WeeklyUsageAdjustment, AppendOnlyAdmin)
admin.site.register(ServiceFulfilment, AppendOnlyAdmin)
admin.site.register(ServiceFulfilmentEvent, AppendOnlyAdmin)
