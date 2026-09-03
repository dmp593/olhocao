from django.contrib import admin

from discounts.models import Promotion, PromotionChangeEvent, PromotionRedemption


class ReadOnlyAuditAdmin(admin.ModelAdmin):
    actions = None

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)


@admin.register(Promotion)
class PromotionAdmin(ReadOnlyAuditAdmin):
    list_display = ("code", "name", "discount_type", "value", "is_active", "starts_at", "ends_at")
    list_filter = ("is_active", "discount_type")
    search_fields = ("code", "name")


@admin.register(PromotionRedemption)
class PromotionRedemptionAdmin(ReadOnlyAuditAdmin):
    list_display = (
        "code_snapshot",
        "customer",
        "purchase_kind",
        "status",
        "discount_amount_eur",
        "reserved_at",
    )
    list_filter = ("status", "purchase_kind")
    search_fields = ("code_snapshot", "purchase_reference", "customer__user__email")


@admin.register(PromotionChangeEvent)
class PromotionChangeEventAdmin(ReadOnlyAuditAdmin):
    list_display = ("promotion", "change_type", "actor", "created_at")
