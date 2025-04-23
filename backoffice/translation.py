from modeltranslation.translator import translator, TranslationOptions

from .models import (
    LegalDocument, LegalDocumentSection, LegalDocumentSectionLineItem
)


class LegalDocumentTranslationOptions(TranslationOptions):
    fields = ('title',)


class LegalDocumentSectionTranslationOptions(TranslationOptions):
    fields = ('title',)


class LegalDocumentSectionLineItemTranslationOption(TranslationOptions):
    fields = ('text',)


translator.register(
    LegalDocument, LegalDocumentTranslationOptions
)

translator.register(
    LegalDocumentSection, LegalDocumentSectionTranslationOptions
)

translator.register(
    LegalDocumentSectionLineItem, LegalDocumentSectionLineItemTranslationOption
)
