from django import forms
from django.contrib.auth import forms as auth_forms
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from . import models


class SignUpForm(auth_forms.UserCreationForm):
    first_name = forms.CharField(
        max_length=50,
        required=True,
        label=_("First Name"),
        help_text=_("Enter your legal first name as it appears on official documents."),
    )

    last_name = forms.CharField(
        max_length=50,
        required=True,
        label=_("Last Name"),
        help_text=_("Enter your legal last name as it appears on official documents."),
    )

    vat = forms.CharField(
        max_length=30,
        required=True,
        label=_("VAT Number"),
        help_text=_("Your tax identification number (required for invoicing)."),
    )

    phone = forms.CharField(
        max_length=30,
        required=True,
        label=_("Phone Number"),
        help_text=_("We'll use this to contact you about your account or services."),
    )

    email = forms.EmailField(
        required=True,
        label=_("Email Address"),
        help_text=_("Your primary email address for billing and account notifications."),
    )

    class Meta:
        model = get_user_model()
        fields = (
            "first_name",
            "last_name",
            "vat",
            "phone",
            "email",
            "username",
            "password1",
            "password2",
        )

    def save(self, commit: bool = True):
        instance = super().save(commit)
        if not commit:
            return instance
        customer_profile, _ = models.CustomerProfile.objects.get_or_create(user=instance)
        customer_profile.billing_phone = self.cleaned_data["phone"]
        customer_profile.tax_number = self.cleaned_data["vat"]
        customer_profile.toconline_sync_status = models.CustomerSyncStatus.PENDING
        customer_profile.toconline_sync_error = ""
        customer_profile.save(
            update_fields=(
                "billing_phone",
                "tax_number",
                "toconline_sync_status",
                "toconline_sync_error",
            )
        )
        return instance


class UserChangeForm(auth_forms.UserChangeForm):
    first_name = forms.CharField(
        max_length=50,
        required=True,
        label=_("First Name"),
        help_text=_("Enter your legal first name as it appears on official documents."),
    )

    last_name = forms.CharField(
        max_length=50,
        required=True,
        label=_("Last Name"),
        help_text=_("Enter your legal last name as it appears on official documents."),
    )

    vat = forms.CharField(
        max_length=30,
        required=True,
        label=_("VAT Number"),
        help_text=_("Your tax identification number (required for invoicing)."),
    )

    phone = forms.CharField(
        max_length=30,
        required=True,
        label=_("Phone Number"),
        help_text=_("We'll use this to contact you about your account or services."),
    )

    email = forms.EmailField(
        required=True,
        label=_("Email Address"),
        help_text=_("Your primary email address for billing and account notifications."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and hasattr(self.instance, "customer_profile"):
            self.fields["vat"].initial = self.instance.customer_profile.tax_number
            self.fields["phone"].initial = self.instance.customer_profile.billing_phone

    class Meta:
        model = get_user_model()
        fields = [
            "username",
            "first_name",
            "last_name",
            "vat",
            "phone",
            "email",
        ]

    def save(self, commit: bool = True):
        instance = super().save(commit)
        if not commit:
            return instance
        customer_profile, _ = models.CustomerProfile.objects.get_or_create(user=instance)
        customer_profile.billing_phone = self.cleaned_data["phone"]
        customer_profile.tax_number = self.cleaned_data["vat"]
        customer_profile.toconline_sync_status = models.CustomerSyncStatus.PENDING
        customer_profile.toconline_sync_error = ""
        customer_profile.save(
            update_fields=(
                "billing_phone",
                "tax_number",
                "toconline_sync_status",
                "toconline_sync_error",
            )
        )
        return instance
