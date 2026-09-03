from modeltranslation.translator import TranslationOptions, translator

from .models import PetSpecies


class PetSpeciesTranslationOptions(TranslationOptions):
    fields = ("name",)


translator.register(PetSpecies, PetSpeciesTranslationOptions)
