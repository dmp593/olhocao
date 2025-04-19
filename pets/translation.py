from modeltranslation.translator import translator, TranslationOptions
from .models import PetSpecies


class PetSpeciesTranslationOptions(TranslationOptions):
    fields = ('name',)


translator.register(PetSpecies, PetSpeciesTranslationOptions)
