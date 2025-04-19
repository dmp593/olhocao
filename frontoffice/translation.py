from modeltranslation.translator import translator, TranslationOptions
from .models import ContactSubject


class ContactSubjectTranslationOptions(TranslationOptions):
    fields = ('name',)


translator.register(ContactSubject, ContactSubjectTranslationOptions)
