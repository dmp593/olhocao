from django.forms import ModelForm


from . import models


class ContactUsForm(ModelForm):
    class Meta:
        model = models.ContactRequest
        fields = [
            'subject',
            'name',
            'phone',
            'email',
            'message',
        ]
