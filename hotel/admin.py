from django.contrib import admin

from .models import Booking, BookingStay, BookingService, BookingStayMedia


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
	list_display = ("id", "account", "status", "created_at", "paid_at")
	list_filter = ("status",)
	search_fields = ("id", "account__user__username", "account__user__email")


@admin.register(BookingStay)
class BookingStayAdmin(admin.ModelAdmin):
	list_display = ("id", "booking", "pet", "start_date", "end_date")
	search_fields = ("booking__id", "pet__name")


@admin.register(BookingService)
class BookingServiceAdmin(admin.ModelAdmin):
	list_display = ("id", "stay", "pet", "stripe_product_id", "quantity")
	search_fields = ("stay__booking__id", "pet__name", "stripe_product_id")


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
