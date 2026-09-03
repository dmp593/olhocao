from modeltranslation.translator import TranslationOptions, translator

from .models import CatalogLevel, CatalogOffer, CatalogType


class CatalogTypeTranslationOptions(TranslationOptions):
    fields = ("name", "description", "image_alt")


class CatalogLevelTranslationOptions(TranslationOptions):
    fields = ("name", "description", "image_alt")


class CatalogOfferTranslationOptions(TranslationOptions):
    fields = ("name", "description", "image_alt")


translator.register(CatalogType, CatalogTypeTranslationOptions)
translator.register(CatalogLevel, CatalogLevelTranslationOptions)
translator.register(CatalogOffer, CatalogOfferTranslationOptions)
