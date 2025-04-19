from django.contrib import admin
from modeltranslation.admin import TranslationAdmin


from . import models


@admin.register(models.PetSpecies)
class PetSpeciesAdmin(TranslationAdmin):
    list_display = ('name', )
    ordering = ('name', )
    search_fields = ('name', )
    list_filter = ('name', )


admin.site.register(models.Pet)
