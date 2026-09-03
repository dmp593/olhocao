from datetime import timedelta

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from activities.models import (
    OperationalClosure,
    SessionSeries,
    Trainer,
    WeeklyUsageAdjustment,
)
from catalog.models import CatalogLevel, CatalogType, CatalogWorkflow

WEEKDAY_CHOICES = (
    (1, _("Monday")),
    (2, _("Tuesday")),
    (3, _("Wednesday")),
    (4, _("Thursday")),
    (5, _("Friday")),
    (6, _("Saturday")),
    (7, _("Sunday")),
)


class TrainerForm(forms.ModelForm):
    class Meta:
        model = Trainer
        fields = (
            "user",
            "name",
            "email",
            "phone",
            "biography",
            "image",
            "image_alt",
            "is_active",
        )
        widgets = {"biography": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = self.fields["user"].queryset.filter(is_staff=True)
        self.fields["user"].help_text = _(
            "Optional. Link a staff login when this trainer also uses the management area."
        )


class SessionSeriesForm(forms.ModelForm):
    weekdays = forms.MultipleChoiceField(
        choices=WEEKDAY_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label=_("Days of the week"),
    )

    class Meta:
        model = SessionSeries
        fields = (
            "catalog_type",
            "title",
            "starts_on",
            "ends_on",
            "weekdays",
            "starts_at_time",
            "ends_at_time",
            "capacity_override",
            "eligible_levels",
            "trainers",
            "skip_closed_days",
            "notes",
            "is_active",
        )
        widgets = {
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "ends_on": forms.DateInput(attrs={"type": "date"}),
            "starts_at_time": forms.TimeInput(attrs={"type": "time"}),
            "ends_at_time": forms.TimeInput(attrs={"type": "time"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        scheduled_types = CatalogType.objects.filter(
            workflow__in=(CatalogWorkflow.FULL_DAY_SLOT, CatalogWorkflow.TIMED_SLOT),
            is_active=True,
        )
        self.fields["catalog_type"].queryset = scheduled_types
        self.fields["eligible_levels"].queryset = CatalogLevel.objects.filter(
            catalog_type__in=scheduled_types,
            is_active=True,
        ).select_related("catalog_type")
        self.fields["trainers"].queryset = Trainer.objects.filter(is_active=True)
        self.fields["eligible_levels"].help_text = _(
            "Leave empty to accept every level of the selected activity."
        )
        self.fields["capacity_override"].help_text = _(
            "Leave empty to inherit the activity capacity. "
            "Each generated session stores a snapshot."
        )
        if self.instance.pk:
            self.initial["weekdays"] = [str(day) for day in self.instance.weekdays]

    def clean_weekdays(self):
        return [int(day) for day in self.cleaned_data["weekdays"]]

    def clean(self):
        cleaned_data = super().clean()
        catalog_type = cleaned_data.get("catalog_type")
        levels = cleaned_data.get("eligible_levels")
        trainers = cleaned_data.get("trainers")
        if catalog_type and levels and levels.exclude(catalog_type=catalog_type).exists():
            self.add_error(
                "eligible_levels",
                _("Every selected level must belong to the selected activity."),
            )
        if catalog_type and catalog_type.workflow == CatalogWorkflow.TIMED_SLOT and not trainers:
            self.add_error("trainers", _("Timed sessions require at least one trainer."))
        return cleaned_data


class OperationalClosureForm(forms.ModelForm):
    class Meta:
        model = OperationalClosure
        fields = (
            "name",
            "source",
            "starts_at",
            "ends_at",
            "all_operational_services",
            "catalog_types",
            "reason",
            "customer_message",
            "is_active",
        )
        widgets = {
            "starts_at": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M",
                attrs={"type": "datetime-local"},
            ),
            "ends_at": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M",
                attrs={"type": "datetime-local"},
            ),
            "reason": forms.Textarea(attrs={"rows": 3}),
            "customer_message": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["catalog_types"].queryset = CatalogType.objects.filter(
            is_active=True,
        ).exclude(workflow=CatalogWorkflow.DATE_RANGE)
        self.fields["all_operational_services"].label = _("Close every non-accommodation service")
        self.fields["all_operational_services"].help_text = _(
            "Hotel-style date-range accommodation remains available online."
        )
        self.fields["reason"].help_text = _("Required internal audit reason.")
        self.fields["customer_message"].help_text = _(
            "Optional public explanation. Never include private staff details."
        )

    def clean(self):
        cleaned_data = super().clean()
        all_operational_services = cleaned_data.get("all_operational_services")
        catalog_types = cleaned_data.get("catalog_types")
        if not all_operational_services and not catalog_types:
            self.add_error(
                "catalog_types",
                _("Choose at least one service type or close every operational service."),
            )
        if all_operational_services and catalog_types:
            self.add_error(
                "catalog_types",
                _("When closing every operational service, leave the service types empty."),
            )
        return cleaned_data


class WeeklyUsageAdjustmentForm(forms.ModelForm):
    class Meta:
        model = WeeklyUsageAdjustment
        fields = ("week_start", "extra_uses", "reason")
        widgets = {
            "week_start": forms.DateInput(attrs={"type": "date"}),
            "reason": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, entitlement, **kwargs):
        self.entitlement = entitlement
        super().__init__(*args, **kwargs)
        self.initial.setdefault(
            "week_start",
            timezone.localdate() - timedelta(days=timezone.localdate().weekday()),
        )

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.entitlement = self.entitlement
        if commit:
            instance.full_clean()
            instance.save()
        return instance
