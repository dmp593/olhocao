from django.contrib import admin
from django.utils.timezone import now
from modeltranslation.admin import TranslationAdmin

from . import models


@admin.register(models.ContactSubject)
class ContactSubjectAdmin(TranslationAdmin):
    list_display = ("name",)
    search_fields = ("name",)
    ordering = ("order",)


@admin.register(models.ContactRequest)
class ContactRequestAdmin(admin.ModelAdmin):
    list_display = ("subject", "name", "email", "phone")
    list_filter = ("subject",)
    search_fields = ("subject", "name", "email", "phone", "message")

    readonly_fields = ("subject", "name", "email", "phone", "message", "created_at", "read_at")

    actions = ["mark_as_read"]

    def mark_as_read(self, request, queryset):
        updated_count = queryset.update(read_at=now())

        self.message_user(request, f"{updated_count} contact requests marked as read.")

    mark_as_read.short_description = "Mark selected contact requests as read"


@admin.register(models.SiteSettings)
class SiteSettingsAdmin(TranslationAdmin):
    readonly_fields = ("updated_at", "updated_by")

    def has_add_permission(self, request):
        return not models.SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class OrderedContentAdmin(TranslationAdmin):
    list_display = ("__str__", "order", "is_active", "updated_at")
    list_editable = ("order", "is_active")
    ordering = ("order", "id")


admin.site.register(models.HomeSlide, OrderedContentAdmin)
admin.site.register(models.ServiceHighlight, OrderedContentAdmin)
admin.site.register(models.Testimonial, OrderedContentAdmin)
admin.site.register(models.TeamMember, OrderedContentAdmin)
admin.site.register(models.Partner, OrderedContentAdmin)
admin.site.register(models.SocialLink, admin.ModelAdmin)
