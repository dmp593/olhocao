from django import forms
from django.db.models import IntegerField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from activities.models import (
    EntitlementStatus,
    OfferingMode,
    ServiceEntitlement,
)
from catalog.models import CatalogWorkflow
from hotel.models import ServiceSchedulePreference
from pets.models import Pet


class PurchaseServiceForm(forms.Form):
    pet = forms.ModelChoiceField(
        queryset=Pet.objects.none(),
        label=_("Dog"),
        empty_label=_("Choose a dog"),
    )
    promotion_code = forms.CharField(
        required=False,
        max_length=40,
        label=_("Promotion code"),
        widget=forms.TextInput(
            attrs={
                "class": "ui-field uppercase",
                "autocomplete": "off",
                "placeholder": _("Optional"),
            }
        ),
    )
    schedule_preference = forms.ChoiceField(
        required=False,
        choices=ServiceSchedulePreference.choices,
        label=_("When would you prefer the service?"),
    )
    preferred_date = forms.DateField(
        required=False,
        label=_("Preferred date"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    preferred_time = forms.TimeField(
        required=False,
        label=_("Preferred time"),
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    customer_notes = forms.CharField(
        required=False,
        label=_("Notes for the team"),
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(
        self,
        *args,
        customer,
        offering,
        fixed_pet=None,
        collect_schedule_preference=True,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.offering = offering
        self.fields["pet"].queryset = Pet.objects.filter(
            owner=customer,
            deleted_at__isnull=True,
        ).order_by("name")
        if fixed_pet is not None:
            self.fields["pet"].queryset = self.fields["pet"].queryset.filter(pk=fixed_pet.pk)
            self.fields["pet"].initial = fixed_pet
            self.fields["pet"].widget = forms.HiddenInput()
        self.collects_schedule_preference = (
            collect_schedule_preference
            and offering.mode == OfferingMode.ONE_OFF
            and offering.catalog_offer.catalog_type.workflow == CatalogWorkflow.FLEXIBLE
        )
        if not self.collects_schedule_preference:
            for field_name in (
                "schedule_preference",
                "preferred_date",
                "preferred_time",
                "customer_notes",
            ):
                self.fields.pop(field_name)
        else:
            self.fields["schedule_preference"].initial = ServiceSchedulePreference.UNSCHEDULED

    def clean(self):
        cleaned_data = super().clean()
        if not self.collects_schedule_preference:
            return cleaned_data
        preference = cleaned_data.get("schedule_preference")
        preferred_date = cleaned_data.get("preferred_date")
        preferred_time = cleaned_data.get("preferred_time")
        if preferred_date and preferred_date < timezone.localdate():
            self.add_error("preferred_date", _("The preferred date cannot be in the past."))
        if preference == ServiceSchedulePreference.UNSCHEDULED:
            if preferred_date:
                self.add_error("preferred_date", _("Leave the date empty for no preference."))
            if preferred_time:
                self.add_error("preferred_time", _("Leave the time empty for no preference."))
        elif preference == ServiceSchedulePreference.DATE:
            if not preferred_date:
                self.add_error("preferred_date", _("Choose a preferred date."))
            if preferred_time:
                self.add_error(
                    "preferred_time",
                    _("Choose date and time if you want to include a time."),
                )
        elif preference == ServiceSchedulePreference.DATE_TIME:
            if not preferred_date:
                self.add_error("preferred_date", _("Choose a preferred date."))
            if not preferred_time:
                self.add_error("preferred_time", _("Choose a preferred time."))
        return cleaned_data


class ReserveSessionForm(forms.Form):
    entitlement = forms.ModelChoiceField(
        queryset=ServiceEntitlement.objects.none(),
        label=_("Service or pack"),
        empty_label=_("Choose a service or pack"),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label=_("Notes for the team"),
    )

    def __init__(self, *args, customer, session, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = (
            ServiceEntitlement.objects.filter(
                customer=customer,
                catalog_type_reference=session.catalog_type_id,
                access_blocked_at__isnull=True,
            )
            .filter(
                Q(current_period_start__isnull=True)
                | Q(current_period_start__lte=session.starts_at),
                Q(current_period_end__isnull=True) | Q(current_period_end__gt=session.starts_at),
            )
            .filter(
                Q(
                    status__in=(
                        EntitlementStatus.ACTIVE,
                        EntitlementStatus.CANCEL_AT_PERIOD_END,
                    ),
                    current_period_end__isnull=True,
                )
                | Q(
                    status__in=(
                        EntitlementStatus.ACTIVE,
                        EntitlementStatus.CANCEL_AT_PERIOD_END,
                        EntitlementStatus.PAST_DUE,
                    ),
                    current_period_end__gt=timezone.now(),
                )
            )
        )
        queryset = queryset.annotate(
            available_credits=Coalesce(
                Sum("credit_entries__delta"),
                Value(0),
                output_field=IntegerField(),
            )
        ).filter(
            Q(offering_mode_snapshot=OfferingMode.SUBSCRIPTION)
            | Q(
                offering_mode_snapshot__in=(OfferingMode.ONE_OFF, OfferingMode.PACK),
                available_credits__gt=0,
            )
        )
        eligible_level_ids = session.eligible_levels.values_list("pk", flat=True)
        if eligible_level_ids:
            queryset = queryset.filter(catalog_level_reference__in=eligible_level_ids)
        self.fields["entitlement"].queryset = queryset.select_related(
            "pet",
            "offering__catalog_offer__catalog_item",
        )


class FlexibleServiceRequestForm(forms.Form):
    schedule_preference = forms.ChoiceField(
        choices=ServiceSchedulePreference.choices,
        label=_("When would you prefer the service?"),
    )
    preferred_date = forms.DateField(
        required=False,
        label=_("Preferred date"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    preferred_time = forms.TimeField(
        required=False,
        label=_("Preferred time"),
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    customer_notes = forms.CharField(
        required=False,
        label=_("Notes for the team"),
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def clean(self):
        cleaned_data = super().clean()
        preference = cleaned_data.get("schedule_preference")
        preferred_date = cleaned_data.get("preferred_date")
        preferred_time = cleaned_data.get("preferred_time")
        if preferred_date and preferred_date < timezone.localdate():
            self.add_error("preferred_date", _("The preferred date cannot be in the past."))
        if preference == ServiceSchedulePreference.UNSCHEDULED:
            if preferred_date:
                self.add_error("preferred_date", _("Leave the date empty for no preference."))
            if preferred_time:
                self.add_error("preferred_time", _("Leave the time empty for no preference."))
        elif preference == ServiceSchedulePreference.DATE:
            if not preferred_date:
                self.add_error("preferred_date", _("Choose a preferred date."))
            if preferred_time:
                self.add_error("preferred_time", _("Choose date and time to include a time."))
        elif preference == ServiceSchedulePreference.DATE_TIME:
            if not preferred_date:
                self.add_error("preferred_date", _("Choose a preferred date."))
            if not preferred_time:
                self.add_error("preferred_time", _("Choose a preferred time."))
        return cleaned_data


class CancelFlexibleServiceForm(forms.Form):
    reason = forms.CharField(
        max_length=500,
        label=_("Why do you need to cancel?"),
        widget=forms.Textarea(attrs={"rows": 3}),
    )
