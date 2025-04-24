from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth import forms as auth_forms
from django.utils.translation import gettext_lazy as _

from . import models


class UserChangeForm(auth_forms.UserChangeForm):
    first_name = forms.CharField(
        max_length=50,
        required=True,
        label=_("First Name"),
        help_text=_("Enter your legal first name as it appears on official documents.")
    )

    last_name = forms.CharField(
        max_length=50,
        required=True,
        label=_("Last Name"),
        help_text=_("Enter your legal last name as it appears on official documents.")
    )

    vat = forms.CharField(
        max_length=30,
        required=True,
        label=_("VAT Number"),
        help_text=_("Your tax identification number (required for invoicing).")
    )

    phone = forms.CharField(
        max_length=30,
        required=True,
        label=_("Phone Number"),
        help_text=_("We'll use this to contact you about your account or services.")
    )

    email = forms.EmailField(
        required=True,
        label=_("Email Address"),
        help_text=_("Your primary email address for account notifications.")
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        initial = getattr(self, 'initial', None)

        if initial and 'vat' in initial:
            self.fields['vat'].widget.attrs['readonly'] = True

    class Meta:
        model = get_user_model()
        fields = [
            'username',
            'first_name',
            'last_name',
            'vat',
            'phone',
            'email',
        ]

    def save(self, commit: bool = True):
        instance = super().save(commit)
        account, created = models.Account.objects.get_or_create(user=instance)
        return instance
