from django import forms
from django.utils.translation import gettext_lazy as _
from datetime import date


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
                    field_name = f"pet-{pet.id}-service-{service.id}"
                    self.fields[field_name] = forms.IntegerField(
                        required=False,
                        min_value=0,
                        max_value=duration,
                        initial=0,
                        widget=forms.NumberInput(attrs={
                            'class': 'service-quantity',
                            'data-pet-id': pet.id,
                            'data-service-id': service.id,
                            'min': 0,
                            'max': duration,
                        })
                    )
                    self.fields[field_name].label = f"{pet.name} - {service.name}"

    def clean(self):
        cleaned_data = super().clean()
        # You can add additional validation here if needed
        return cleaned_data


class BookingPaymentForm(forms.Form):
    terms_accepted = forms.BooleanField(
        required=True,
        label=_("I agree to the terms and conditions"),
        error_messages={
            'required': _('You must accept the terms and conditions')
        }
    )

    special_requests = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        label=_("Special requests or instructions")
    )


class BookingModifyForm(forms.Form):
    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(
            attrs={
                'type': 'date',
                'class': 'block w-full px-3 py-2 border border-primary-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-accent-500 focus:border-transparent'
            }
        ),
        label=_("Start date")
    )

    def __init__(self, *args, original_start=None, original_end=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_start = original_start
        self.original_end = original_end
        self.duration = (self.original_end - self.original_start).days if self.original_start and self.original_end else None
        # Disable field if not allowed to modify (7 days rule)
        if self.original_start and (self.original_start - date.today()).days < 7:
            self.fields['start_date'].disabled = True
        # Disable if outside 6 month window from original dates
        if self.original_start and (date.today() - self.original_start).days > 183:
            self.fields['start_date'].disabled = True

    def clean(self):
        from datetime import timedelta
        cleaned_data = super().clean()
        start = cleaned_data.get('start_date')

        # Only allow changes/cancels up to 7 days before original start
        if self.original_start and (self.original_start - date.today()).days < 7:
            raise forms.ValidationError(_("Changes or cancellations must be made at least 7 days before the start date."))
        # Only allow changes within 6 months of original start
        if self.original_start and (date.today() - self.original_start).days > 183:
            raise forms.ValidationError(_("You can only modify or cancel your booking within 6 months of the original booking date."))

        # If changing dates, must be same length (enforced by auto-calculation)
        if self.duration is not None and start:
            if start < date.today():
                raise forms.ValidationError(_("Start date cannot be in the past."))
            # Calculate end date
            cleaned_data['end_date'] = start + timedelta(days=self.duration)
        return cleaned_data
