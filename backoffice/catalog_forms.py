from django import forms
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from activities.models import EasypayFrequency, OfferingMode, ServiceOffering
from catalog.models import (
    CatalogLevel,
    CatalogOffer,
    CatalogType,
    CatalogWorkflow,
)
from catalog.services.add_ons import optional_service_offers
from hotel.models import (
    CatalogBookingKind,
    CatalogItem,
    CatalogItemOrigin,
    CatalogItemType,
    CatalogSyncStatus,
)

VAT_RATE_CHOICES = (
    ("", _("Choose a VAT rate")),
    ("NOR", _("Normal VAT rate")),
    ("INT", _("Intermediate VAT rate")),
    ("RED", _("Reduced VAT rate")),
    ("ISE", _("VAT exempt")),
)


class CatalogTypeForm(forms.ModelForm):
    """Edits customer-facing taxonomy separately from fiscal sale records."""

    class Meta:
        model = CatalogType
        fields = (
            "name_pt",
            "name_en",
            "slug",
            "description_pt",
            "description_en",
            "workflow",
            "default_capacity",
            "default_cancellation_notice_hours",
            "closes_on_national_holidays",
            "image",
            "image_alt_pt",
            "image_alt_en",
            "order",
            "is_active",
        )
        widgets = {
            "description_pt": forms.Textarea(attrs={"rows": 3}),
            "description_en": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name_pt"].label = _("Name (Portuguese)")
        self.fields["name_en"].label = _("Name (English)")
        self.fields["description_pt"].label = _("Description (Portuguese)")
        self.fields["description_en"].label = _("Description (English)")
        self.fields["image_alt_pt"].label = _("Image description (Portuguese)")
        self.fields["image_alt_en"].label = _("Image description (English)")
        self.fields["workflow"].help_text = _(
            "This stable behavior controls nights, full days, timed sessions or a simple sale. "
            "The public name can be changed later without changing reservations."
        )

    def clean(self):
        cleaned_data = super().clean()
        cleaned_data["name"] = cleaned_data.get("name_pt", "")
        cleaned_data["description"] = cleaned_data.get("description_pt", "")
        cleaned_data["image_alt"] = cleaned_data.get("image_alt_pt", "")
        return cleaned_data


class CatalogLevelForm(forms.ModelForm):
    class Meta:
        model = CatalogLevel
        fields = (
            "name_pt",
            "name_en",
            "slug",
            "description_pt",
            "description_en",
            "capacity_override",
            "cancellation_notice_hours_override",
            "image",
            "image_alt_pt",
            "image_alt_en",
            "order",
            "is_active",
        )
        widgets = {
            "description_pt": forms.Textarea(attrs={"rows": 3}),
            "description_en": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, catalog_type, **kwargs):
        self.catalog_type = catalog_type
        super().__init__(*args, **kwargs)
        self.fields["name_pt"].label = _("Name (Portuguese)")
        self.fields["name_en"].label = _("Name (English)")
        self.fields["description_pt"].label = _("Description (Portuguese)")
        self.fields["description_en"].label = _("Description (English)")
        self.fields["image_alt_pt"].label = _("Image description (Portuguese)")
        self.fields["image_alt_en"].label = _("Image description (English)")

    def clean(self):
        cleaned_data = super().clean()
        cleaned_data["name"] = cleaned_data.get("name_pt", "")
        cleaned_data["description"] = cleaned_data.get("description_pt", "")
        cleaned_data["image_alt"] = cleaned_data.get("image_alt_pt", "")
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.catalog_type = self.catalog_type
        if commit:
            instance.save()
        return instance


class CatalogOfferForm(forms.ModelForm):
    """Maintains one sellable price or plan and its TOConline projection.

    The taxonomy and presentation live on ``CatalogOffer``. Fiscal fields live
    on ``CatalogItem`` and get a successor instead of being rewritten once the
    item has been published or originated in TOConline.
    """

    toconline_type = forms.ChoiceField(
        choices=(("", _("Choose product or service")), *CatalogItemType.choices),
        label=_("Fiscal type"),
    )
    unit_price_eur = forms.DecimalField(
        min_value=0,
        max_digits=12,
        decimal_places=2,
        label=_("Price (EUR, VAT included)"),
    )
    tax_code = forms.ChoiceField(choices=VAT_RATE_CHOICES, label=_("VAT rate"))
    sale_mode = forms.ChoiceField(
        required=False,
        choices=(("", _("Choose how it is sold")), *OfferingMode.choices),
        label=_("Plan format"),
    )
    included_sessions = forms.IntegerField(
        required=False,
        min_value=1,
        label=_("Sessions included in the pack"),
    )
    sessions_per_week = forms.IntegerField(
        required=False,
        min_value=1,
        label=_("Uses per week"),
    )
    validity_days = forms.IntegerField(
        required=False,
        min_value=1,
        label=_("Pack validity in days"),
    )
    billing_frequency = forms.ChoiceField(
        required=False,
        choices=(("", _("Choose a billing frequency")), *EasypayFrequency.choices),
        label=_("Billing frequency"),
    )
    cancellation_notice_hours = forms.IntegerField(
        required=False,
        min_value=0,
        label=_("Cancellation notice in hours"),
    )

    FISCAL_FIELDS = ("toconline_type", "unit_price_eur", "tax_code")

    class Meta:
        model = CatalogOffer
        fields = (
            "name_pt",
            "name_en",
            "description_pt",
            "description_en",
            "level",
            "image",
            "image_alt_pt",
            "image_alt_en",
            "is_standalone",
            "is_public",
            "is_active",
            "order",
        )
        widgets = {
            "description_pt": forms.Textarea(attrs={"rows": 3}),
            "description_en": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, catalog_type, **kwargs):
        self.catalog_type = catalog_type
        super().__init__(*args, **kwargs)
        self.fields["level"].queryset = catalog_type.levels.filter(is_active=True)
        self.fields["level"].empty_label = _("No level")
        self.fields["name_pt"].label = _("Price or plan name (Portuguese)")
        self.fields["name_en"].label = _("Price or plan name (English)")
        self.fields["description_pt"].label = _("Description (Portuguese)")
        self.fields["description_en"].label = _("Description (English)")
        self.fields["image_alt_pt"].label = _("Image description (Portuguese)")
        self.fields["image_alt_en"].label = _("Image description (English)")
        self.fields["image"].help_text = _(
            "Optional. Without an image, the level image is used, then the catalog type image."
        )

        if self.instance.pk:
            item = self.instance.catalog_item
            self.initial.update(
                {
                    "toconline_type": item.toconline_type,
                    "unit_price_eur": item.unit_price_eur,
                    "tax_code": item.tax_code,
                }
            )
            offering = getattr(self.instance, "activity_offering", None)
            if offering:
                self.initial.update(
                    {
                        "sale_mode": offering.mode,
                        "included_sessions": offering.included_sessions,
                        "sessions_per_week": offering.sessions_per_week,
                        "validity_days": offering.validity_days,
                        "billing_frequency": offering.billing_frequency,
                        "cancellation_notice_hours": offering.cancellation_notice_hours,
                    }
                )
        elif catalog_type.workflow != CatalogWorkflow.DATE_RANGE:
            self.initial["toconline_type"] = CatalogItemType.SERVICE
            self.initial["cancellation_notice_hours"] = (
                catalog_type.default_cancellation_notice_hours
            )

    def clean_tax_code(self):
        tax_code = self.cleaned_data["tax_code"].strip().upper()
        if tax_code not in {"NOR", "INT", "RED", "ISE"}:
            raise forms.ValidationError(
                _("Use a supported Portuguese VAT code: NOR, INT, RED or ISE.")
            )
        return tax_code

    def clean(self):
        cleaned_data = super().clean()
        cleaned_data["name"] = cleaned_data.get("name_pt", "")
        cleaned_data["description"] = cleaned_data.get("description_pt", "")
        cleaned_data["image_alt"] = cleaned_data.get("image_alt_pt", "")

        level = cleaned_data.get("level")
        if level and level.catalog_type_id != self.catalog_type.pk:
            self.add_error("level", _("The level must belong to this catalog type."))

        if cleaned_data.get("is_public") and not cleaned_data.get("is_active"):
            self.add_error("is_public", _("Only an active price or plan can be public."))

        if self.catalog_type.workflow != CatalogWorkflow.DATE_RANGE:
            if (
                self.catalog_type.is_scheduled
                and cleaned_data.get("toconline_type") != CatalogItemType.SERVICE
            ):
                self.add_error(
                    "toconline_type",
                    _("Services booked on a calendar must be fiscal services."),
                )
            self._validate_scheduling_terms(cleaned_data)
        return cleaned_data

    def _validate_scheduling_terms(self, cleaned_data):
        mode = cleaned_data.get("sale_mode")
        if mode == OfferingMode.ONE_OFF:
            return
        if mode == OfferingMode.PACK:
            if not cleaned_data.get("included_sessions"):
                self.add_error("included_sessions", _("Enter the pack size."))
            if not cleaned_data.get("validity_days"):
                self.add_error("validity_days", _("Enter the pack validity."))
        elif mode == OfferingMode.SUBSCRIPTION:
            if not cleaned_data.get("sessions_per_week"):
                self.add_error("sessions_per_week", _("Enter the weekly allowance."))
            if not cleaned_data.get("billing_frequency"):
                self.add_error("billing_frequency", _("Choose the billing frequency."))
        else:
            self.add_error(
                "sale_mode",
                _("Choose a one-off purchase, pack or subscription."),
            )

    @transaction.atomic
    def save(self, commit=True):
        if not commit:
            raise ValueError("CatalogOfferForm only supports commit=True.")

        offer = super().save(commit=False)
        offer.catalog_type = self.catalog_type
        offer.name = self.cleaned_data["name_pt"]
        offer.description = self.cleaned_data.get("description_pt", "")
        offer.image_alt = self.cleaned_data.get("image_alt_pt", "")

        item = self._save_fiscal_projection(offer)
        offer.catalog_item = item
        offer.full_clean()
        offer.save()
        self.save_m2m()
        self._save_sales_terms(offer)
        self.instance = offer
        return offer

    def _save_fiscal_projection(self, offer):
        fiscal_values = {
            "toconline_type": self.cleaned_data["toconline_type"],
            "name": self.cleaned_data["name_pt"],
            "description": self.cleaned_data.get("description_pt", ""),
            "unit_price_eur": self.cleaned_data["unit_price_eur"],
            "tax_code": self.cleaned_data["tax_code"],
            "currency": "EUR",
            "is_active": self.cleaned_data["is_active"],
            "is_public": self.cleaned_data["is_public"],
            "is_configured": True,
            "booking_kind": (
                CatalogBookingKind.STAY
                if self.catalog_type.workflow == CatalogWorkflow.DATE_RANGE
                else CatalogBookingKind.OTHER
            ),
        }
        if not offer.pk:
            item = CatalogItem.objects.create(
                **fiscal_values,
                origin=CatalogItemOrigin.WEBSITE,
                sync_status=CatalogSyncStatus.PENDING,
            )
            self._assign_managed_item_code(item)
            return item

        current = CatalogItem.objects.select_for_update().get(pk=offer.catalog_item_id)
        fiscal_changed = any(
            getattr(current, field_name) != fiscal_values[field_name]
            for field_name in (*self.FISCAL_FIELDS, "name")
        )
        immutable_projection = current.origin == CatalogItemOrigin.TOCONLINE or bool(
            current.toconline_id
        )
        if fiscal_changed and immutable_projection:
            successor = CatalogItem.objects.create(
                **fiscal_values,
                origin=CatalogItemOrigin.WEBSITE,
                sync_status=CatalogSyncStatus.PENDING,
                supersedes=current,
            )
            self._assign_managed_item_code(successor)
            current.is_active = False
            current.is_public = False
            current.save(update_fields=("is_active", "is_public", "updated_at"))
            return successor

        for field_name, value in fiscal_values.items():
            setattr(current, field_name, value)
        if fiscal_changed:
            current.sync_status = CatalogSyncStatus.PENDING
            current.sync_error = ""
        current.save()
        return current

    @staticmethod
    def _assign_managed_item_code(item):
        marker = "S" if item.toconline_type == CatalogItemType.SERVICE else "P"
        item.item_code = f"OC-{marker}-{item.pk:08d}"
        item.save(update_fields=("item_code", "updated_at"))

    def _save_sales_terms(self, offer):
        existing = getattr(offer, "activity_offering", None)
        if self.catalog_type.workflow == CatalogWorkflow.DATE_RANGE:
            if existing and existing.is_published:
                existing.is_published = False
                existing.save(update_fields=("is_published", "updated_at"))
            return

        mode = self.cleaned_data["sale_mode"]
        values = {
            "mode": mode,
            "included_sessions": (
                self.cleaned_data.get("included_sessions") if mode == OfferingMode.PACK else None
            ),
            "sessions_per_week": (
                self.cleaned_data.get("sessions_per_week")
                if mode == OfferingMode.SUBSCRIPTION
                else None
            ),
            "validity_days": (
                self.cleaned_data.get("validity_days") if mode == OfferingMode.PACK else None
            ),
            "billing_frequency": (
                self.cleaned_data.get("billing_frequency", "")
                if mode == OfferingMode.SUBSCRIPTION
                else ""
            ),
            "cancellation_notice_hours": (
                self.cleaned_data.get("cancellation_notice_hours")
                if self.cleaned_data.get("cancellation_notice_hours") is not None
                else offer.effective_cancellation_notice_hours
            ),
            "is_published": self.cleaned_data["is_public"],
        }
        offering, _ = ServiceOffering.objects.update_or_create(
            catalog_offer=offer,
            defaults=values,
        )
        offering.full_clean()
        offering.save()


class OptionalServiceConfigurationForm(forms.Form):
    """Bulk editor for optional services attached to one catalog type or level."""

    target = forms.ChoiceField(label=_("Service destination"))
    add_on_ids = forms.ModelMultipleChoiceField(
        label=_("Associated optional services"),
        queryset=CatalogOffer.objects.none(),
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        catalog_types = CatalogType.objects.prefetch_related("levels").order_by("order", "name")
        self.fields["target"].choices = self._target_choices(catalog_types)
        self.fields["add_on_ids"].queryset = optional_service_offers()
        self.target_type = None
        self.target_level = None

    @staticmethod
    def _target_choices(catalog_types):
        choices = []
        for catalog_type in catalog_types:
            type_choices = [
                (f"type:{catalog_type.pk}", _("Every level in this type")),
                *((f"level:{level.pk}", level.name) for level in catalog_type.levels.all()),
            ]
            choices.append((catalog_type.name, type_choices))
        return choices

    def clean_target(self):
        value = self.cleaned_data["target"]
        self.target_type, self.target_level = resolve_optional_service_target(value)
        return value


def resolve_optional_service_target(value):
    """Resolve a signed-in staff selection without relying on commercial names."""

    try:
        target_kind, raw_pk = str(value).split(":", maxsplit=1)
        target_pk = int(raw_pk)
    except (TypeError, ValueError) as exc:
        raise forms.ValidationError(_("Choose a valid service destination.")) from exc

    if target_kind == "type":
        target_type = CatalogType.objects.filter(pk=target_pk).first()
        if target_type is not None:
            return target_type, None
    elif target_kind == "level":
        target_level = (
            CatalogLevel.objects.select_related("catalog_type").filter(pk=target_pk).first()
        )
        if target_level is not None:
            return target_level.catalog_type, target_level
    raise forms.ValidationError(_("Choose a valid service destination."))
