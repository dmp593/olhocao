from django import forms
from django.db.models import Q
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from accounts.models import CustomerProfile
from catalog.models import AddOnAvailability, CatalogLevel, CatalogOffer, CatalogType
from discounts.models import Promotion, SaleChannel
from discounts.widgets import AsyncMultiSelectWidget
from pets.models import Pet


class PromotionForm(forms.ModelForm):
    """Management form for capability-based promotion rules.

    Catalog names are deliberately absent from the business rules. Staff first
    choose stable sale channels and can optionally narrow the rule to dynamic
    catalog types, levels, prices or plans.
    """

    eligible_sale_channels = forms.MultipleChoiceField(
        choices=SaleChannel.choices,
        label=_("Eligible purchase flows"),
        help_text=_("Choose how an eligible price or plan is being purchased."),
        widget=forms.CheckboxSelectMultiple(attrs={"class": "ui-checkbox"}),
    )
    eligible_breeds = forms.Field(
        required=False,
        label=_("Eligible breeds"),
        help_text=_(
            "Optional. Search the breeds already registered on customer dogs. "
            "Spelling and letter case are normalized when matching."
        ),
        widget=AsyncMultiSelectWidget,
    )

    class Meta:
        model = Promotion
        fields = (
            "code",
            "name",
            "discount_type",
            "value",
            "eligible_sale_channels",
            "catalog_types",
            "catalog_levels",
            "catalog_offers",
            "host_catalog_types",
            "customers",
            "pets",
            "eligible_breeds",
            "starts_at",
            "ends_at",
            "service_starts_on",
            "service_ends_on",
            "minimum_eligible_amount_eur",
            "max_redemptions",
            "max_redemptions_per_customer",
            "is_active",
        )
        widgets = {
            "customers": AsyncMultiSelectWidget,
            "pets": AsyncMultiSelectWidget,
            "starts_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "ends_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "service_starts_on": forms.DateInput(attrs={"type": "date"}),
            "service_ends_on": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {
            "code": _("Code"),
            "name": _("Internal name"),
            "discount_type": _("Discount type"),
            "value": _("Value"),
            "catalog_types": _("Specific catalog types"),
            "catalog_levels": _("Specific levels"),
            "catalog_offers": _("Specific prices or plans"),
            "host_catalog_types": _("Main services containing the optional service"),
            "customers": _("Specific customers"),
            "pets": _("Specific dogs"),
            "starts_at": _("Code available from"),
            "ends_at": _("Code available until"),
            "service_starts_on": _("First eligible service date"),
            "service_ends_on": _("Last eligible service date"),
            "minimum_eligible_amount_eur": _("Minimum eligible subtotal (€)"),
            "max_redemptions": _("Total usage limit"),
            "max_redemptions_per_customer": _("Usage limit per customer"),
            "is_active": _("Active"),
        }
        help_texts = {
            "code": _("Customers enter this code at checkout."),
            "name": _("A clear description for the management team."),
            "catalog_types": _("Leave empty to include every catalog type."),
            "catalog_levels": _("Leave empty to include every level."),
            "catalog_offers": _("Leave empty to include every matching price or plan."),
            "host_catalog_types": _(
                "Only applies to the optional-service purchase flow. Choose the main services "
                "where the discounted optional service must have been added; leave empty for all."
            ),
            "customers": _(
                "Leave empty to include every customer. Search results are loaded in pages."
            ),
            "pets": _(
                "Leave empty unless the promotion is for named dogs. Search by dog, breed or owner."
            ),
            "service_starts_on": _(
                "Optional. The stay or dated optional service must start on or after this day."
            ),
            "service_ends_on": _(
                "Optional. The stay or dated optional service must finish on or before this day."
            ),
            "minimum_eligible_amount_eur": _(
                "Calculated only from the items that match this promotion, before discount."
            ),
            "max_redemptions": _("Leave empty for no global limit."),
            "max_redemptions_per_customer": _("Leave empty for no per-customer limit."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        selected_type_ids = self._selected_ids("catalog_types")
        selected_level_ids = self._selected_ids("catalog_levels")
        selected_offer_ids = self._selected_ids("catalog_offers")
        selected_host_type_ids = set(self._selected_ids("host_catalog_types"))
        selected_host_type_ids.update(self._submitted_or_initial_values("host_catalog_types"))
        selected_customer_ids = self._selected_ids("customers")
        selected_pet_ids = self._selected_ids("pets")

        self.fields["catalog_types"].queryset = CatalogType.objects.filter(
            Q(is_active=True) | Q(pk__in=selected_type_ids)
        ).distinct()
        self.fields["catalog_levels"].queryset = (
            CatalogLevel.objects.select_related("catalog_type")
            .filter(Q(is_active=True) | Q(pk__in=selected_level_ids))
            .distinct()
        )
        self.fields["catalog_offers"].queryset = (
            CatalogOffer.objects.select_related("catalog_type", "level", "catalog_item")
            .filter(Q(is_active=True) | Q(pk__in=selected_offer_ids))
            .distinct()
        )
        active_host_type_ids = AddOnAvailability.objects.filter(is_active=True).values_list(
            "target_type_id", flat=True
        )
        self.fields["host_catalog_types"].queryset = (
            CatalogType.objects.filter(
                Q(pk__in=active_host_type_ids) | Q(pk__in=selected_host_type_ids)
            )
            .distinct()
            .order_by("order", "name")
        )
        self.fields["customers"].queryset = (
            CustomerProfile.objects.select_related("user")
            .filter(user__is_staff=False)
            .filter(Q(user__is_active=True) | Q(pk__in=selected_customer_ids))
            .distinct()
            .order_by("user__first_name", "user__last_name", "user__email")
        )
        self.fields["pets"].queryset = (
            Pet.objects.select_related("owner__user")
            .filter(Q(deleted_at__isnull=True) | Q(pk__in=selected_pet_ids))
            .filter(Q(owner__user__is_active=True) | Q(pk__in=selected_pet_ids))
            .distinct()
            .order_by("name", "owner__user__email")
        )
        self.fields["starts_at"].input_formats = ("%Y-%m-%dT%H:%M",)
        self.fields["ends_at"].input_formats = ("%Y-%m-%dT%H:%M",)
        if not self.is_bound and self.instance.pk:
            self.initial["eligible_breeds"] = list(self.instance.eligible_breeds or [])
        self._configure_async_audience_fields()

    def _selected_ids(self, field_name):
        if not self.instance.pk:
            return ()
        return tuple(getattr(self.instance, field_name).values_list("pk", flat=True))

    def clean_eligible_breeds(self):
        raw_value = self.cleaned_data.get("eligible_breeds") or []
        return [
            value.strip()
            for raw_item in raw_value
            for value in str(raw_item).split(",")
            if value.strip()
        ]

    def clean(self):
        cleaned_data = super().clean()
        self._validate_catalog_hierarchy(cleaned_data)
        self._validate_add_on_scope(cleaned_data)
        self._validate_customer_pets(cleaned_data)
        self._validate_service_date_scope(cleaned_data)
        return cleaned_data

    def _configure_async_audience_fields(self):
        self._configure_async_field(
            "customers",
            search_kind="customers",
            options=(
                {
                    "value": customer.pk,
                    "label": customer.user.get_full_name() or customer.user.username,
                    "description": customer.user.email,
                }
                for customer in self.fields["customers"].queryset.filter(
                    pk__in=self._submitted_or_initial_values("customers")
                )
            ),
        )
        self._configure_async_field(
            "pets",
            search_kind="pets",
            options=(
                {
                    "value": pet.pk,
                    "label": pet.name,
                    "description": _("%(breed)s · %(owner)s")
                    % {"breed": pet.breed, "owner": str(pet.owner)},
                }
                for pet in self.fields["pets"].queryset.filter(
                    pk__in=self._submitted_or_initial_values("pets")
                )
            ),
        )
        breed_values = self._submitted_or_initial_values("eligible_breeds")
        self._configure_async_field(
            "eligible_breeds",
            search_kind="breeds",
            options=({"value": breed, "label": breed, "description": ""} for breed in breed_values),
        )

    def _configure_async_field(self, field_name, *, search_kind, options):
        widget = self.fields[field_name].widget
        widget.search_url = reverse("discounts:promotion_audience_search", args=(search_kind,))
        widget.selected_options = tuple(options)

    def _submitted_or_initial_values(self, field_name):
        def identifiers(values):
            return tuple(getattr(value, "pk", value) for value in values)

        if self.is_bound:
            key = self.add_prefix(field_name)
            if hasattr(self.data, "getlist"):
                return identifiers(
                    value for value in self.data.getlist(key) if value not in (None, "")
                )
            raw_value = self.data.get(key, ())
            if raw_value in (None, ""):
                return ()
            if isinstance(raw_value, (list, tuple)):
                return identifiers(raw_value)
            return (raw_value,)
        initial = self.initial.get(field_name)
        if initial is not None:
            if isinstance(initial, str):
                return (initial,) if initial else ()
            return identifiers(initial)
        return self._selected_ids(field_name)

    def _validate_catalog_hierarchy(self, cleaned_data):
        type_ids = {item.pk for item in cleaned_data.get("catalog_types") or ()}
        levels = tuple(cleaned_data.get("catalog_levels") or ())
        offers = tuple(cleaned_data.get("catalog_offers") or ())
        level_ids = {level.pk for level in levels}

        invalid_levels = [
            level.name for level in levels if type_ids and level.catalog_type_id not in type_ids
        ]
        if invalid_levels:
            self.add_error(
                "catalog_levels",
                _("These levels are outside the selected catalog types: %(levels)s.")
                % {"levels": ", ".join(invalid_levels)},
            )

        invalid_offers = [
            offer.name
            for offer in offers
            if (type_ids and offer.catalog_type_id not in type_ids)
            or (level_ids and offer.level_id not in level_ids)
        ]
        if invalid_offers:
            self.add_error(
                "catalog_offers",
                _("These prices or plans are outside the selected catalog scope: %(offers)s.")
                % {"offers": ", ".join(invalid_offers)},
            )

    def _validate_add_on_scope(self, cleaned_data):
        host_types = tuple(cleaned_data.get("host_catalog_types") or ())
        channels = set(cleaned_data.get("eligible_sale_channels") or ())
        if host_types and SaleChannel.ADD_ON not in channels:
            self.add_error(
                "host_catalog_types",
                _("Choose the add-on purchase flow before restricting host services."),
            )

    def _validate_service_date_scope(self, cleaned_data):
        if not (cleaned_data.get("service_starts_on") or cleaned_data.get("service_ends_on")):
            return
        compatible_channels = {SaleChannel.DATE_RANGE, SaleChannel.ADD_ON}
        selected_channels = set(cleaned_data.get("eligible_sale_channels") or ())
        unsupported_channels = selected_channels - compatible_channels
        if unsupported_channels:
            self.add_error(
                "service_starts_on",
                _(
                    "Service-date restrictions only work with stays and optional services "
                    "attached to a dated reservation. Create a separate promotion for packs, "
                    "subscriptions or standalone purchases."
                ),
            )

    def _validate_customer_pets(self, cleaned_data):
        customers = tuple(cleaned_data.get("customers") or ())
        pets = tuple(cleaned_data.get("pets") or ())
        if not customers or not pets:
            return
        customer_ids = {customer.pk for customer in customers}
        pets_from_other_customers = [pet.name for pet in pets if pet.owner_id not in customer_ids]
        if pets_from_other_customers:
            self.add_error(
                "pets",
                _("These dogs do not belong to the selected customers: %(pets)s.")
                % {"pets": ", ".join(pets_from_other_customers)},
            )


class PromotionFilterForm(forms.Form):
    query = forms.CharField(
        required=False,
        label=_("Promotion"),
        widget=forms.TextInput(attrs={"placeholder": _("Code or internal name")}),
    )
    status = forms.ChoiceField(
        required=False,
        label=_("Status"),
        choices=(
            ("", _("All promotions")),
            ("active", _("Active")),
            ("inactive", _("Inactive")),
        ),
    )

    def filter(self, queryset):
        if not self.is_valid():
            return queryset
        query = self.cleaned_data["query"]
        if query:
            queryset = queryset.filter(Q(code__icontains=query) | Q(name__icontains=query))
        status = self.cleaned_data["status"]
        if status:
            queryset = queryset.filter(is_active=status == "active")
        return queryset
