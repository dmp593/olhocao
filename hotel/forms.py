from datetime import timedelta
from decimal import Decimal

from django import forms
from django.core.validators import MinLengthValidator
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class BookingStayForm(forms.Form):
    offer_id = forms.CharField(required=True)
    pet_ids = forms.MultipleChoiceField(required=True, widget=forms.CheckboxSelectMultiple)
    start_date = forms.DateField(required=True)
    end_date = forms.DateField(required=True)

    def __init__(self, *args, pets=None, **kwargs):
        super().__init__(*args, **kwargs)
        if pets:
            self.fields["pet_ids"].choices = [(pet.id, pet.name) for pet in pets]

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")

        if start_date and start_date < timezone.localdate():
            self.add_error("start_date", _("Check-in cannot be in the past."))
        if start_date and end_date and end_date <= start_date:
            self.add_error(
                "end_date",
                _("Check-out must be after check-in."),
            )
        return cleaned_data


class PetServiceForm(forms.Form):
    """Collect optional add-ons and scheduling preferences per dog."""

    def __init__(
        self,
        *args,
        pets=None,
        services=None,
        duration=None,
        start_date=None,
        end_date=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.duration = duration
        self.start_date = start_date
        self.end_date = end_date
        self.pets = tuple(pets or ())
        self.services = tuple(services or ())

        for pet in self.pets:
            for service in self.services:
                prefix = self.field_prefix(pet.pk, service.pk)
                self.fields[f"{prefix}-quantity"] = forms.IntegerField(
                    required=False,
                    min_value=0,
                    max_value=duration,
                    initial=0,
                    widget=forms.NumberInput(
                        attrs={
                            "class": "ui-field service-quantity",
                            "data-pet-id": pet.id,
                            "data-offer-id": service.id,
                            "data-service-quantity": "",
                            "min": 0,
                            "max": duration,
                        }
                    ),
                )
                self.fields[f"{prefix}-schedule"] = forms.ChoiceField(
                    required=False,
                    choices=(
                        ("unscheduled", _("No date preference")),
                        ("date", _("Preferred date")),
                        ("date_time", _("Preferred date and time")),
                    ),
                    initial="unscheduled",
                    widget=forms.Select(
                        attrs={
                            "class": "ui-field",
                            "data-schedule-preference": "",
                        }
                    ),
                )
                self.fields[f"{prefix}-date"] = forms.DateField(
                    required=False,
                    widget=forms.DateInput(
                        attrs={
                            "type": "date",
                            "class": "ui-field",
                            "data-preferred-date": "",
                            "min": start_date.isoformat() if start_date else "",
                            "max": ((end_date - timedelta(days=1)).isoformat() if end_date else ""),
                        }
                    ),
                )
                self.fields[f"{prefix}-time"] = forms.TimeField(
                    required=False,
                    widget=forms.TimeInput(
                        attrs={
                            "type": "time",
                            "class": "ui-field",
                            "data-preferred-time": "",
                        }
                    ),
                )

    @staticmethod
    def field_prefix(pet_id, offer_id):
        return f"pet-{pet_id}-offer-{offer_id}"

    @property
    def selection_rows(self):
        rows = []
        for service in self.services:
            pet_fields = []
            for pet in self.pets:
                prefix = self.field_prefix(pet.pk, service.pk)
                pet_fields.append(
                    {
                        "pet": pet,
                        "quantity": self[f"{prefix}-quantity"],
                        "schedule": self[f"{prefix}-schedule"],
                        "date": self[f"{prefix}-date"],
                        "time": self[f"{prefix}-time"],
                    }
                )
            rows.append({"service": service, "pet_fields": pet_fields})
        return rows

    def clean(self):
        cleaned_data = super().clean()
        for pet in self.pets:
            for service in self.services:
                prefix = self.field_prefix(pet.pk, service.pk)
                quantity = cleaned_data.get(f"{prefix}-quantity") or 0
                preference = cleaned_data.get(f"{prefix}-schedule") or "unscheduled"
                preferred_date = cleaned_data.get(f"{prefix}-date")
                preferred_time = cleaned_data.get(f"{prefix}-time")

                if quantity == 0:
                    continue
                if preference in {"date", "date_time"} and not preferred_date:
                    self.add_error(
                        f"{prefix}-date",
                        _("Choose a preferred date or select no date preference."),
                    )
                if preference == "date_time" and not preferred_time:
                    self.add_error(f"{prefix}-time", _("Choose a preferred time."))
                if preference != "date_time" and preferred_time:
                    self.add_error(
                        f"{prefix}-time",
                        _("Choose the date and time option to include a time."),
                    )
                if preference == "unscheduled" and preferred_date:
                    self.add_error(
                        f"{prefix}-date",
                        _("Choose a date preference before entering a date."),
                    )
                if (
                    preferred_date
                    and self.start_date
                    and self.end_date
                    and not self.start_date <= preferred_date < self.end_date
                ):
                    self.add_error(
                        f"{prefix}-date",
                        _("The preferred date must be before check-out and within the stay."),
                    )
        return cleaned_data

    def selected_services(self):
        """Return a serializable selection after successful validation."""

        if not self.is_valid():
            raise ValueError("selected_services() requires a valid form")
        selections = {}
        for pet in self.pets:
            for service in self.services:
                prefix = self.field_prefix(pet.pk, service.pk)
                quantity = self.cleaned_data.get(f"{prefix}-quantity") or 0
                if quantity == 0:
                    continue
                preferred_date = self.cleaned_data.get(f"{prefix}-date")
                preferred_time = self.cleaned_data.get(f"{prefix}-time")
                selections.setdefault(str(pet.pk), {})[str(service.pk)] = {
                    "quantity": quantity,
                    "schedule_preference": self.cleaned_data.get(f"{prefix}-schedule")
                    or "unscheduled",
                    "preferred_date": (preferred_date.isoformat() if preferred_date else None),
                    "preferred_time": (
                        preferred_time.isoformat(timespec="minutes") if preferred_time else None
                    ),
                }
        return selections


class BookingPaymentForm(forms.Form):
    promotion_code = forms.CharField(required=False, widget=forms.HiddenInput)
    terms_accepted = forms.BooleanField(
        required=True,
        label=_("I agree to the terms and conditions"),
        error_messages={"required": _("You must accept the terms and conditions")},
    )

    special_requests = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label=_("Special requests or instructions"),
    )


class BookingModifyForm(forms.Form):
    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "ui-field",
            }
        ),
        label=_("Start date"),
    )

    def __init__(
        self,
        *args,
        original_start=None,
        original_end=None,
        enforce_customer_policy=True,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.original_start = original_start
        self.original_end = original_end
        self.enforce_customer_policy = enforce_customer_policy
        self.duration = (
            (self.original_end - self.original_start).days
            if self.original_start and self.original_end
            else None
        )
        today = timezone.localdate()
        if (
            self.enforce_customer_policy
            and self.original_start
            and (self.original_start - today).days < 7
        ):
            self.fields["start_date"].disabled = True
        if (
            self.enforce_customer_policy
            and self.original_start
            and (today - self.original_start).days > 183
        ):
            self.fields["start_date"].disabled = True

    def clean(self):
        from datetime import timedelta

        cleaned_data = super().clean()
        start = cleaned_data.get("start_date")

        today = timezone.localdate()
        if (
            self.enforce_customer_policy
            and self.original_start
            and (self.original_start - today).days < 7
        ):
            raise forms.ValidationError(
                _("Changes or cancellations must be made at least 7 days before the start date.")
            )
        if (
            self.enforce_customer_policy
            and self.original_start
            and (today - self.original_start).days > 183
        ):
            raise forms.ValidationError(
                _(
                    "You can only modify or cancel your booking within 6 months "
                    "of the original booking date."
                )
            )

        if self.duration is not None and start:
            if start < today:
                raise forms.ValidationError(_("Start date cannot be in the past."))
            cleaned_data["end_date"] = start + timedelta(days=self.duration)
        return cleaned_data


class PromotionCodeForm(forms.Form):
    promotion_code = forms.CharField(
        max_length=40,
        label=_("Promotion code"),
        widget=forms.TextInput(
            attrs={
                "class": "ui-field uppercase",
                "autocomplete": "off",
                "placeholder": _("Enter code"),
            }
        ),
    )


class BookingPriceAdjustmentForm(forms.Form):
    final_price_eur = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=0,
        label=_("New final price"),
        widget=forms.NumberInput(attrs={"class": "ui-field", "step": "0.01"}),
    )
    reason = forms.CharField(
        max_length=500,
        validators=[MinLengthValidator(5)],
        label=_("Reason for the change"),
        help_text=_("Required and stored in the staff-only financial audit history."),
        widget=forms.Textarea(attrs={"class": "ui-field", "rows": 3}),
    )

    def __init__(self, *args, booking, **kwargs):
        super().__init__(*args, **kwargs)
        self.booking = booking
        self.fields["final_price_eur"].max_value = booking.gross_total_price_eur
        self.fields["final_price_eur"].initial = booking.total_price_eur


class BookingRefundForm(forms.Form):
    amount_eur = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label=_("Amount to refund"),
        widget=forms.NumberInput(attrs={"class": "ui-field", "step": "0.01"}),
    )
    reason = forms.CharField(
        max_length=500,
        validators=[MinLengthValidator(5)],
        label=_("Reason for the refund"),
        help_text=_("Required and stored permanently in the financial audit history."),
        widget=forms.Textarea(attrs={"class": "ui-field", "rows": 3}),
    )

    def __init__(self, *args, refundable_amount, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["amount_eur"].max_value = refundable_amount
        self.fields["amount_eur"].initial = refundable_amount
