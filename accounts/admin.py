from django.contrib import admin

from accounts.models import CustomerProfile


@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "tax_number",
        "billing_phone",
        "toconline_sync_status",
        "toconline_synced_at",
    )
    list_filter = ("toconline_sync_status",)
    search_fields = ("user__username", "user__email", "tax_number", "billing_phone")
    readonly_fields = (
        "toconline_customer_id",
        "toconline_sync_status",
        "toconline_sync_error",
        "toconline_sync_attempted_at",
        "toconline_synced_at",
    )
