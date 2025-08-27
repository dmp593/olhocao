from django.contrib import admin
from modeltranslation.admin import TranslationAdmin
from django.utils.timezone import now

from . import models


@admin.register(models.ContactSubject)
class ContactSubjectAdmin(TranslationAdmin):
    list_display = ('name', )
    search_fields = ('name', )
    ordering = ('order', )


@admin.register(models.ContactRequest)
class ContactRequestAdmin(admin.ModelAdmin):
    list_display = ('subject', 'name', 'email', 'phone')
    list_filter = ('subject', )
    search_fields = ('subject', 'name', 'email', 'phone', 'message')

    readonly_fields = (
        'subject',
        'name',
        'email',
        'phone',
        'message',
        'created_at',
        'read_at'
    )

    actions = ['mark_as_read']

    def mark_as_read(self, request, queryset):
        updated_count = queryset.update(read_at=now())

        self.message_user(
            request, f"{updated_count} contact requests marked as read."
        )

    mark_as_read.short_description = "Mark selected contact requests as read"
