from django import forms
from django.forms import modelformset_factory, inlineformset_factory
from .models import (
    LegalDocument, LegalDocumentSection, LegalDocumentSectionLineItem
)


class LegalDocumentForm(forms.ModelForm):
    class Meta:
        model = LegalDocument
        fields = ["doc_type", "title", "effective_date"]
        widgets = {
            'effective_date': forms.DateInput(
                format='%Y-%m-%d',
                attrs={
                    'type': 'date',
                    'class': 'block w-full px-4 py-3 border border-primary-200 rounded-lg focus:ring-2 focus:ring-accent-500 focus:border-transparent font-sans bg-white'
                }
            ),
        }

class LegalDocumentSectionForm(forms.ModelForm):
    class Meta:
        model = LegalDocumentSection
        fields = ["title", "order"]
        widgets = {
            "order": forms.HiddenInput()
        }


class LegalDocumentSectionLineItemForm(forms.ModelForm):
    class Meta:
        model = LegalDocumentSectionLineItem
        fields = ["text", "order"]
        widgets = {
            "text": forms.Textarea(
                attrs={
                    'rows': 3,
                }
            ),
            "order": forms.HiddenInput(),
        }


def create_lineitem_formset(extra: int = 1, can_delete: bool = True):
    return inlineformset_factory(
        LegalDocumentSection,
        LegalDocumentSectionLineItem,
        form=LegalDocumentSectionLineItemForm,
        extra=extra,
        can_delete=can_delete
    )



def create_section_formset(extra: int = 1, can_delete: bool = True):
    return inlineformset_factory(
        LegalDocument,
        LegalDocumentSection,
        form=LegalDocumentSectionForm,
        extra=extra,
        can_delete=can_delete
    )
