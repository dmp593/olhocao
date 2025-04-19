from django.contrib import admin
from modeltranslation.admin import TranslationAdmin

from . import models


@admin.register(models.ContactSubject)
class ContactSubjectAdmin(TranslationAdmin):
    list_display = ('name', )
    search_fields = ('name', )
    ordering = ('order', )
