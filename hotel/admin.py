from django.contrib import admin

from .models import (
    Booking,
    BookingEvent,
    BookingPriceAdjustment,
    BookingRefund,
    BookingService,
    BookingStay,
    BookingStayMedia,
    CatalogItem,
    PaymentAttempt,
    PaymentEvent,
)


class ReadOnlyFinancialAdmin(admin.ModelAdmin):
    actions = None

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)


@admin.register(CatalogItem)
class CatalogItemAdmin(admin.ModelAdmin):
    list_display = (
        "item_code",
        "name",
        "family_name",
        "unit_price_eur",
        "booking_kind",
        "is_public",
        "is_active",
        "has_image",
        "last_seen_at",
    )
    list_filter = (
        "booking_kind",
        "is_public",
        "is_active",
        "toconline_type",
        "family_name",
    )
    search_fields = ("item_code", "name", "family_name", "toconline_id")
    readonly_fields = (
        "toconline_id",
        "toconline_type",
        "item_code",
        "name",
        "description",
        "family_id",
        "family_name",
        "unit_price_eur",
        "tax_code",
        "currency",
        "is_active",
        "last_seen_at",
        "remote_deleted_at",
        "created_at",
        "updated_at",
    )

    @admin.display(boolean=True, description="Image")
    def has_image(self, item):
        return bool(item.image)


@admin.register(Booking)
class BookingAdmin(ReadOnlyFinancialAdmin):
    list_display = (
        "id",
        "customer",
        "status",
        "document_status",
        "created_at",
        "paid_at",
    )
    list_filter = ("status", "document_status")
    search_fields = ("id", "customer__user__username", "customer__user__email")


@admin.register(BookingStay)
class BookingStayAdmin(ReadOnlyFinancialAdmin):
    list_display = ("id", "booking", "pet", "start_date", "end_date")
    search_fields = ("booking__id", "pet__name")


@admin.register(BookingService)
class BookingServiceAdmin(ReadOnlyFinancialAdmin):
    list_display = ("id", "stay", "pet", "item_name_snapshot", "quantity")
    search_fields = (
        "stay__booking__id",
        "pet__name",
        "item_name_snapshot",
        "toconline_item_id",
    )


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(ReadOnlyFinancialAdmin):
    list_display = (
        "merchant_reference",
        "booking",
        "provider",
        "amount_eur",
        "status",
        "payment_method",
        "created_at",
    )
    list_filter = ("provider", "status", "payment_method")
    search_fields = (
        "merchant_reference",
        "provider_link_id",
        "provider_payment_id",
        "booking__id",
    )


@admin.register(BookingRefund)
class BookingRefundAdmin(ReadOnlyFinancialAdmin):
    list_display = (
        "payment_attempt",
        "amount_eur",
        "status",
        "requested_by",
        "requested_at",
    )
    list_filter = ("status", "requested_at")
    search_fields = (
        "refund_reference",
        "provider_refund_id",
        "payment_attempt__merchant_reference",
        "payment_attempt__booking__id",
    )


@admin.register(PaymentEvent)
class PaymentEventAdmin(ReadOnlyFinancialAdmin):
    list_display = (
        "fingerprint",
        "payment_attempt",
        "event_type",
        "provider_status",
        "received_at",
    )
    search_fields = ("fingerprint", "provider_event_id", "payment_attempt__merchant_reference")


@admin.register(BookingEvent)
class BookingEventAdmin(ReadOnlyFinancialAdmin):
    list_display = ("booking", "event_type", "actor", "created_at")
    list_filter = ("event_type",)
    search_fields = ("booking__id", "actor__username")


@admin.register(BookingPriceAdjustment)
class BookingPriceAdjustmentAdmin(ReadOnlyFinancialAdmin):
    list_display = (
        "booking",
        "previous_total_amount_eur",
        "new_total_amount_eur",
        "actor",
        "created_at",
    )
    search_fields = ("booking__id", "actor__username", "reason")


@admin.register(BookingStayMedia)
class BookingStayMediaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "stay",
        "original_filename",
        "content_type",
        "size",
        "created_by",
        "created_at",
    )
    search_fields = ("original_filename", "stay__booking__id", "stay__pet__name")
