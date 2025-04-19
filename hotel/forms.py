from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


class BookingStayForm(forms.Form):
    stay_id = forms.CharField(required=True)
    pet_ids = forms.MultipleChoiceField(
        required=True,
        widget=forms.CheckboxSelectMultiple
    )
    start_date = forms.DateField(required=True)
    end_date = forms.DateField(required=True)

    def __init__(self, *args, pets=None, **kwargs):
        super().__init__(*args, **kwargs)
        if pets:
            self.fields['pet_ids'].choices = [
                (pet.id, pet.name) for pet in pets
            ]


class PetServiceForm(forms.Form):
    def __init__(self, *args, pets=None, services=None, duration=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.duration = duration
        
        if pets and services:
            for pet in pets:
                for service in services:
                    field_name = f"pet_{pet.id}_service_{service['id']}"
                    self.fields[field_name] = forms.IntegerField(
                        required=False,
                        min_value=0,
                        max_value=duration,
                        initial=0,
                        widget=forms.NumberInput(attrs={
                            'class': 'service-quantity',
                            'data-pet-id': pet.id,
                            'data-service-id': service['id'],
                            'min': 0,
                            'max': duration,
                        })
                    )
                    self.fields[field_name].label = f"{pet.name} - {service['item_description']}"

    def clean(self):
        cleaned_data = super().clean()
        # You can add additional validation here if needed
        return cleaned_data


class BookingPaymentForm(forms.Form):
    terms_accepted = forms.BooleanField(
        required=True,
        label="I agree to the terms and conditions",
        error_messages={
            'required': 'You must accept the terms and conditions'
        }
    )

    special_requests = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        label="Special requests or instructions"
    )
