from django import forms
from django.utils.translation import gettext_lazy as _

from . import models


class ContactUsForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["subject"].empty_label = _("Choose a subject")
        self.fields["subject"].queryset = models.ContactSubject.objects.filter(
            is_active=True
        ).order_by("order", "id")

    class Meta:
        model = models.ContactRequest
        fields = [
            "subject",
            "name",
            "phone",
            "email",
            "message",
        ]
        help_texts = {
            "subject": _("Choose the reason for your message."),
            "name": "",
            "phone": _("Include the country code, for example +351 912 345 678."),
            "email": "",
            "message": "",
        }
        widgets = {"message": forms.Textarea(attrs={"rows": 6})}
