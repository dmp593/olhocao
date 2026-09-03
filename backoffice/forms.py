from urllib.parse import urlparse

from django import forms
from django.contrib.auth import get_user_model
from django.core.validators import MinLengthValidator
from django.db import transaction
from django.db.models import Q
from django.forms import inlineformset_factory
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import CustomerProfile
from activities.models import (
    RESERVED_PLACE_STATUSES,
    ActivitySession,
    EntitlementStatus,
    ReservationStatus,
    ServiceFulfilment,
    ServiceFulfilmentStatus,
    Trainer,
)
from activities.services.calendar import closure_reason, trainers_have_conflict
from catalog.models import CatalogLevel, CatalogType, CatalogWorkflow
from frontoffice.models import (
    ContactSubject,
    HomeSlide,
    HomeSlideKind,
    Partner,
    ServiceHighlight,
    SiteSettings,
    SocialLink,
    TeamMember,
    Testimonial,
)
from hotel.models import (
    BookingDocumentStatus,
    BookingStatus,
    CatalogBookingKind,
    CatalogItem,
    CatalogItemOrigin,
    PaymentStatus,
)

from .translation import LegalDocument, LegalDocumentSection, LegalDocumentSectionLineItem


class CustomerInvitationForm(forms.Form):
    first_name = forms.CharField(max_length=50, label=_("First name"))
    last_name = forms.CharField(max_length=50, label=_("Last name"))
    email = forms.EmailField(label=_("Email address"))
    username = forms.CharField(
        max_length=150,
        label=_("Username"),
        help_text=_("The invitation email tells the customer which username to use."),
    )
    phone = forms.CharField(max_length=30, label=_("Phone number"))
    vat = forms.CharField(max_length=30, label=_("VAT number"))

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        User = get_user_model()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(_("A user with this email address already exists."))
        return email

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        User = get_user_model()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError(_("A user with this username already exists."))
        return username

    @transaction.atomic
    def save(self):
        User = get_user_model()
        user = User(
            username=self.cleaned_data["username"],
            first_name=self.cleaned_data["first_name"].strip(),
            last_name=self.cleaned_data["last_name"].strip(),
            email=self.cleaned_data["email"],
            is_active=True,
        )
        user.set_unusable_password()
        user.save()
        CustomerProfile.objects.create(
            user=user,
            billing_phone=self.cleaned_data["phone"].strip(),
            tax_number=self.cleaned_data["vat"].strip(),
        )
        return user


class CustomerListFilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        label=_("Customer"),
        widget=forms.TextInput(attrs={"placeholder": _("Name, email, phone, VAT or username")}),
    )
    status = forms.ChoiceField(
        required=False,
        label=_("Account status"),
        choices=(
            ("", _("All customer accounts")),
            ("active", _("Active")),
            ("inactive", _("Inactive")),
            ("invited", _("Invitation pending")),
        ),
    )


class BookingStayFilterForm(forms.Form):
    DATE_MODE_CHOICES = (
        ("overlap", _("Staying during period")),
        ("checkin", _("Check-in during period")),
        ("checkout", _("Check-out during period")),
    )
    MEDICAL_CHOICES = (
        ("", _("Any care profile")),
        ("medication", _("Medication")),
        ("allergies", _("Allergies")),
        ("notes", _("Special notes")),
    )

    q = forms.CharField(
        required=False,
        label=_("Customer or dog"),
        widget=forms.TextInput(attrs={"placeholder": _("Name, email, phone, VAT or dog")}),
    )
    start_date = forms.DateField(
        required=False,
        label=_("From"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    end_date = forms.DateField(
        required=False,
        label=_("To"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_mode = forms.ChoiceField(
        required=False,
        choices=DATE_MODE_CHOICES,
        label=_("Date meaning"),
    )
    booking_status = forms.ChoiceField(
        required=False,
        choices=(("", _("Any booking status")), *BookingStatus.choices),
        label=_("Booking status"),
    )
    payment_status = forms.ChoiceField(
        required=False,
        choices=(("", _("Any payment status")), *PaymentStatus.choices),
        label=_("Payment status"),
    )
    document_status = forms.ChoiceField(
        required=False,
        choices=(("", _("Any TOConline status")), *BookingDocumentStatus.choices),
        label=_("TOConline document"),
    )
    medical = forms.ChoiceField(
        required=False,
        choices=MEDICAL_CHOICES,
        label=_("Care priority"),
    )
    accommodation = forms.ModelChoiceField(
        required=False,
        queryset=CatalogItem.objects.none(),
        label=_("Accommodation"),
        empty_label=_("All accommodation types"),
    )
    extra = forms.ModelChoiceField(
        required=False,
        queryset=CatalogItem.objects.none(),
        label=_("Extra service"),
        empty_label=_("All extra services"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        available = CatalogItem.objects.filter(is_active=True).filter(
            Q(origin=CatalogItemOrigin.WEBSITE)
            | Q(remote_deleted_at__isnull=True, toconline_id__isnull=False)
        )
        self.fields["accommodation"].queryset = available.filter(
            booking_kind=CatalogBookingKind.STAY
        )
        self.fields["extra"].queryset = available.filter(booking_kind=CatalogBookingKind.EXTRA)

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        if start_date and end_date and start_date > end_date:
            self.add_error("end_date", _("The end date must not be before the start date."))
        return cleaned_data


class ActivityReservationFilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        label=_("Customer or dog"),
        widget=forms.TextInput(attrs={"placeholder": _("Name, email or dog")}),
    )
    start_date = forms.DateField(
        required=False,
        label=_("From"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    end_date = forms.DateField(
        required=False,
        label=_("To"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    catalog_type = forms.ModelChoiceField(
        required=False,
        queryset=CatalogType.objects.none(),
        empty_label=_("All activities"),
        label=_("Activity"),
    )
    status = forms.ChoiceField(
        required=False,
        choices=(("", _("All statuses")), *ReservationStatus.choices),
        label=_("Reservation status"),
    )
    medical_priority = forms.BooleanField(required=False, label=_("Medication or allergies"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["catalog_type"].queryset = CatalogType.objects.filter(
            is_active=True,
        ).exclude(workflow=CatalogWorkflow.DATE_RANGE)

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        if start_date and end_date and start_date > end_date:
            self.add_error("end_date", _("The end date must not be before the start date."))
        return cleaned_data


class SubscriptionFilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        label=_("Customer or dog"),
        widget=forms.TextInput(attrs={"placeholder": _("Name, email or dog")}),
    )
    catalog_type = forms.ModelChoiceField(
        required=False,
        queryset=CatalogType.objects.none(),
        empty_label=_("All activities"),
        label=_("Activity"),
    )
    catalog_level = forms.ModelChoiceField(
        required=False,
        queryset=CatalogLevel.objects.none(),
        empty_label=_("All levels"),
        label=_("Level"),
    )
    status = forms.ChoiceField(
        required=False,
        choices=(("", _("All statuses")), *EntitlementStatus.choices),
        label=_("Subscription status"),
    )
    start_date = forms.DateField(
        required=False,
        label=_("Capacity from"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    end_date = forms.DateField(
        required=False,
        label=_("Capacity to"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    medical_priority = forms.BooleanField(required=False, label=_("Medication or allergies"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        types = CatalogType.objects.filter(
            workflow__in=(CatalogWorkflow.FULL_DAY_SLOT, CatalogWorkflow.TIMED_SLOT),
            is_active=True,
        )
        self.fields["catalog_type"].queryset = types
        self.fields["catalog_level"].queryset = CatalogLevel.objects.filter(
            catalog_type__in=types,
            is_active=True,
        ).select_related("catalog_type")

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        if start_date and end_date and start_date > end_date:
            self.add_error("end_date", _("The end date must not be before the start date."))
        return cleaned_data


class SubscriptionManagementForm(forms.Form):
    SUSPEND = "suspend"
    RESUME = "resume"
    CANCEL_AT_PERIOD_END = "cancel_at_period_end"
    TERMINATE = "terminate"
    BLOCK_ACCESS = "block_access"
    RESTORE_ACCESS = "restore_access"
    SYNCHRONIZE = "synchronize"
    RETRY_DOCUMENTS = "retry_documents"

    ACTION_CHOICES = (
        (SUSPEND, _("Suspend subscription")),
        (RESUME, _("Resume subscription")),
        (CANCEL_AT_PERIOD_END, _("Cancel at period end")),
        (TERMINATE, _("Terminate immediately")),
        (BLOCK_ACCESS, _("Block operational access")),
        (RESTORE_ACCESS, _("Restore operational access")),
        (SYNCHRONIZE, _("Synchronize with Easypay")),
        (RETRY_DOCUMENTS, _("Retry missing documents")),
    )

    action = forms.ChoiceField(choices=ACTION_CHOICES, label=_("Administrative action"))
    reason = forms.CharField(
        max_length=500,
        label=_("Reason"),
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Required for the immutable audit trail."),
    )
    resume_at = forms.DateTimeField(
        required=False,
        label=_("Next charge date"),
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
    )

    def __init__(self, *args, entitlement=None, **kwargs):
        super().__init__(*args, **kwargs)
        if entitlement is None:
            return

        allowed_actions = []
        if entitlement.status in {EntitlementStatus.ACTIVE, EntitlementStatus.PAST_DUE}:
            allowed_actions.extend((self.SUSPEND, self.CANCEL_AT_PERIOD_END, self.TERMINATE))
        elif entitlement.status == EntitlementStatus.CANCEL_AT_PERIOD_END:
            allowed_actions.append(self.TERMINATE)
        elif entitlement.status == EntitlementStatus.SUSPENDED:
            allowed_actions.extend((self.RESUME, self.CANCEL_AT_PERIOD_END, self.TERMINATE))
        elif entitlement.status == EntitlementStatus.PENDING:
            allowed_actions.append(self.TERMINATE)

        allowed_actions.append(
            self.RESTORE_ACCESS if entitlement.access_blocked_at else self.BLOCK_ACCESS
        )
        if entitlement.provider_subscription_id:
            allowed_actions.append(self.SYNCHRONIZE)
        allowed_actions.append(self.RETRY_DOCUMENTS)
        self.fields["action"].choices = [
            choice for choice in self.ACTION_CHOICES if choice[0] in allowed_actions
        ]

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("action") == self.RESUME:
            resume_at = cleaned_data.get("resume_at")
            if resume_at is None:
                self.add_error("resume_at", _("Choose the next charge date."))
            elif resume_at <= timezone.now():
                self.add_error("resume_at", _("The next charge date must be in the future."))
        return cleaned_data


class SubscriptionRefundForm(forms.Form):
    amount = forms.DecimalField(
        min_value=0.01,
        max_digits=12,
        decimal_places=2,
        label=_("Refund amount"),
    )
    reason = forms.CharField(
        max_length=500,
        label=_("Reason"),
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Required for the immutable financial audit trail."),
    )


class EntitlementRefundForm(SubscriptionRefundForm):
    revoke_remaining_access = forms.BooleanField(
        required=False,
        initial=True,
        label=_("Revoke remaining unused access"),
        help_text=_(
            "Cancel open service requests and remove remaining credits "
            "after the refund is confirmed."
        ),
    )


class EntitlementAccessForm(forms.Form):
    BLOCK = "block"
    RESTORE = "restore"

    action = forms.ChoiceField(
        choices=((BLOCK, _("Block access")), (RESTORE, _("Restore access"))),
        widget=forms.HiddenInput,
    )
    reason = forms.CharField(
        max_length=500,
        label=_("Reason"),
        help_text=_("Required for the immutable audit trail."),
    )


class ActivitySessionForm(forms.ModelForm):
    override_operational_rules = forms.BooleanField(
        required=False,
        label=_("Override closure or trainer conflict"),
        help_text=_(
            "Use only for an intentional management exception. The reason is permanently audited."
        ),
    )
    override_reason = forms.CharField(
        required=False,
        max_length=500,
        validators=(MinLengthValidator(5),),
        label=_("Override reason"),
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    class Meta:
        model = ActivitySession
        fields = (
            "title",
            "catalog_type",
            "eligible_levels",
            "trainers",
            "starts_at",
            "ends_at",
            "capacity",
            "notes",
        )
        labels = {
            "title": _("Session name"),
            "catalog_type": _("Activity"),
            "eligible_levels": _("Who can book"),
            "trainers": _("Trainers"),
            "starts_at": _("Start"),
            "ends_at": _("End"),
            "capacity": _("Maximum number of dogs"),
            "notes": _("Internal notes"),
        }
        help_texts = {
            "capacity": _("Maximum number of dogs that can attend this session."),
            "eligible_levels": _(
                "Leave empty so every level of this activity can book, or select specific levels."
            ),
            "trainers": _("Timed training sessions require at least one trainer."),
            "notes": _("Optional information for the team. Customers do not see these notes."),
        }
        widgets = {
            "starts_at": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M",
                attrs={"type": "datetime-local"},
            ),
            "ends_at": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M",
                attrs={"type": "datetime-local"},
            ),
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
        if self.instance.pk and self.instance.is_cancelled:
            for field in self.fields.values():
                field.disabled = True

    def clean_starts_at(self):
        starts_at = self.cleaned_data["starts_at"]
        if starts_at <= timezone.now():
            raise forms.ValidationError(_("The session must start in the future."))
        return starts_at

    def clean(self):
        cleaned_data = super().clean()
        if self.instance.pk and self.instance.is_cancelled:
            raise forms.ValidationError(_("A cancelled session cannot be changed."))
        catalog_type = cleaned_data.get("catalog_type")
        levels = cleaned_data.get("eligible_levels")
        trainers = cleaned_data.get("trainers")
        starts_at = cleaned_data.get("starts_at")
        ends_at = cleaned_data.get("ends_at")
        override = cleaned_data.get("override_operational_rules")
        override_reason = str(cleaned_data.get("override_reason") or "").strip()
        if catalog_type and levels:
            invalid_levels = levels.exclude(catalog_type=catalog_type)
            if invalid_levels.exists():
                self.add_error(
                    "eligible_levels",
                    _("Every selected level must belong to the chosen activity."),
                )
        self._validate_existing_reservations(
            catalog_type=catalog_type,
            levels=levels,
            capacity=cleaned_data.get("capacity"),
        )
        if catalog_type and catalog_type.workflow == CatalogWorkflow.TIMED_SLOT and not trainers:
            self.add_error("trainers", _("Choose at least one trainer."))
        operational_conflicts = []
        if catalog_type and starts_at and ends_at:
            closed_message = closure_reason(catalog_type, starts_at, ends_at)
            if closed_message:
                operational_conflicts.append(
                    _("The centre is closed for this service: %(reason)s")
                    % {"reason": closed_message}
                )
            if trainers and trainers_have_conflict(
                trainers,
                starts_at,
                ends_at,
                exclude_session_id=self.instance.pk,
            ):
                operational_conflicts.append(
                    _("One or more selected trainers already have an overlapping session.")
                )
        if operational_conflicts and not override:
            self.add_error(
                "override_operational_rules",
                _("Resolve the conflict or explicitly authorize an override."),
            )
            for conflict in operational_conflicts:
                self.add_error(None, conflict)
        if override and not override_reason:
            self.add_error("override_reason", _("Explain why this exception is authorized."))
        cleaned_data["operational_conflicts"] = operational_conflicts
        return cleaned_data

    def _validate_existing_reservations(self, *, catalog_type, levels, capacity):
        """Do not silently invalidate places already promised to customers."""

        if not self.instance.pk:
            return
        reservations = self.instance.reservations.filter(
            status__in=RESERVED_PLACE_STATUSES,
        )
        reserved_count = reservations.count()
        if not reserved_count:
            return
        if capacity is not None and capacity < reserved_count:
            self.add_error(
                "capacity",
                _(
                    "This session already has %(count)s reserved places. "
                    "Cancel or move reservations before reducing its capacity."
                )
                % {"count": reserved_count},
            )
        if catalog_type and catalog_type.pk != self.instance.catalog_type_id:
            self.add_error(
                "catalog_type",
                _(
                    "The activity cannot be changed while the session has reservations. "
                    "Cancel or move those reservations first."
                ),
            )
        if not levels:
            return
        selected_level_ids = {level.pk for level in levels}
        reserved_level_ids = set(
            reservations.values_list(
                "entitlement__catalog_level_reference",
                flat=True,
            )
        )
        if None in reserved_level_ids or not reserved_level_ids.issubset(selected_level_ids):
            self.add_error(
                "eligible_levels",
                _(
                    "The selected access levels exclude one or more existing reservations. "
                    "Cancel or move those reservations first."
                ),
            )


class ActivitySessionCancellationForm(forms.Form):
    reason = forms.CharField(
        max_length=500,
        validators=(MinLengthValidator(5),),
        label=_("Cancellation reason"),
        help_text=_(
            "Customers are notified and this reason remains in the operational audit history."
        ),
        widget=forms.Textarea(attrs={"class": "ui-field", "rows": 3}),
    )


class ActivityReservationCancellationForm(forms.Form):
    reason = forms.CharField(
        max_length=500,
        validators=(MinLengthValidator(5),),
        label=_("Cancellation reason"),
        help_text=_("Required for the permanent customer-care history."),
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    restore_entitlement = forms.BooleanField(
        required=False,
        initial=True,
        label=_("Return this use to the customer"),
        help_text=_(
            "When the normal deadline has passed, return the pack credit or weekly use "
            "as a management exception."
        ),
    )


class ServiceFulfilmentManagementForm(forms.Form):
    SCHEDULE = "schedule"
    COMPLETE = "complete"
    CANCEL = "cancel"

    ACTION_CHOICES = (
        (SCHEDULE, _("Schedule or reschedule")),
        (COMPLETE, _("Mark as completed")),
        (CANCEL, _("Cancel service")),
    )

    action = forms.ChoiceField(choices=ACTION_CHOICES, label=_("Action"))
    starts_at = forms.DateTimeField(
        required=False,
        label=_("Appointment start"),
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
    )
    ends_at = forms.DateTimeField(
        required=False,
        label=_("Appointment end"),
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
    )
    reason = forms.CharField(
        max_length=500,
        label=_("Reason"),
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Required for the immutable operational history."),
    )
    internal_notes = forms.CharField(
        required=False,
        label=_("Internal notes"),
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    override_policy = forms.BooleanField(
        required=False,
        label=_("Override closure or cancellation policy"),
        help_text=_("Use only for an intentional management exception; the reason is recorded."),
    )

    def __init__(self, *args, fulfilment: ServiceFulfilment, **kwargs):
        self.fulfilment = fulfilment
        super().__init__(*args, **kwargs)
        if fulfilment.status == ServiceFulfilmentStatus.REQUESTED:
            allowed = {self.SCHEDULE, self.COMPLETE, self.CANCEL}
        elif fulfilment.status == ServiceFulfilmentStatus.SCHEDULED:
            allowed = {self.SCHEDULE, self.COMPLETE, self.CANCEL}
            self.initial.update(
                starts_at=fulfilment.scheduled_starts_at,
                ends_at=fulfilment.scheduled_ends_at,
            )
        else:
            allowed = set()
        self.fields["action"].choices = [
            choice for choice in self.ACTION_CHOICES if choice[0] in allowed
        ]

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("action") == self.SCHEDULE:
            starts_at = cleaned_data.get("starts_at")
            ends_at = cleaned_data.get("ends_at")
            if starts_at is None:
                self.add_error("starts_at", _("Choose the appointment start."))
            if ends_at and starts_at and ends_at <= starts_at:
                self.add_error("ends_at", _("The appointment end must be after its start."))
        return cleaned_data


class LegalDocumentForm(forms.ModelForm):
    class Meta:
        model = LegalDocument
        fields = ["doc_type", "name_pt", "name_en", "heading_pt", "heading_en", "effective_date"]
        widgets = {
            "effective_date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={
                    "type": "date",
                    "class": (
                        "block w-full px-4 py-3 border border-primary-200 "
                        "rounded-lg focus:ring-2 focus:ring-accent-500 "
                        "focus:border-transparent font-sans bg-white"
                    ),
                },
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["doc_type"].choices = (
            ("", _("Choose a document type")),
            *LegalDocument.DocumentType.choices,
        )


class LegalDocumentSectionForm(forms.ModelForm):
    class Meta:
        model = LegalDocumentSection
        fields = ["title_pt", "title_en", "order"]
        widgets = {"order": forms.HiddenInput()}


class LegalDocumentSectionLineItemForm(forms.ModelForm):
    class Meta:
        model = LegalDocumentSectionLineItem
        fields = ["text_pt", "text_en", "order"]
        widgets = {
            "text_pt": forms.Textarea(
                attrs={
                    "rows": 3,
                }
            ),
            "text_en": forms.Textarea(
                attrs={
                    "rows": 3,
                }
            ),
            "order": forms.HiddenInput(),
        }


def create_lineitem_formset(extra: int = 0, can_delete: bool = True):
    return inlineformset_factory(
        LegalDocumentSection,
        LegalDocumentSectionLineItem,
        form=LegalDocumentSectionLineItemForm,
        extra=extra,
        can_delete=can_delete,
    )


def create_section_formset(extra: int = 0, can_delete: bool = True):
    return inlineformset_factory(
        LegalDocument,
        LegalDocumentSection,
        form=LegalDocumentSectionForm,
        extra=extra,
        can_delete=can_delete,
    )


class LocalizedContentForm(forms.ModelForm):
    """Give both supported languages explicit, predictable labels and requirements."""

    translated_labels = {}
    required_translations = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        language_labels = {"pt": _("Portuguese"), "en": _("English")}
        for field_name, label in self.translated_labels.items():
            for language_code, language_label in language_labels.items():
                translated_name = f"{field_name}_{language_code}"
                if translated_name in self.fields:
                    self.fields[translated_name].label = _("%(field)s (%(language)s)") % {
                        "field": label,
                        "language": language_label,
                    }
                    self.fields[translated_name].required = field_name in self.required_translations


def validate_https_url(url):
    """Keep links published by staff on encrypted public destinations."""

    if urlparse(url).scheme != "https":
        raise forms.ValidationError(_("Use an HTTPS URL."))
    return url


class ManagedImageContentForm(LocalizedContentForm):
    def clean(self):
        cleaned_data = super().clean()
        fallback_image = bool(self.instance.pk and self.instance.static_image_path)
        is_being_published = cleaned_data.get("is_active", True)
        if is_being_published and not cleaned_data.get("image") and not fallback_image:
            self.add_error("image", _("Choose an image before publishing this content."))
        return cleaned_data


class SiteSettingsForm(LocalizedContentForm):
    translated_labels = {
        "weekday_hours": _("Weekday hours"),
        "weekend_hours": _("Weekend and holiday hours"),
        "home_services_eyebrow": _("Services eyebrow"),
        "home_services_title": _("Services title"),
        "home_services_subtitle": _("Services introduction"),
        "home_testimonials_eyebrow": _("Testimonials eyebrow"),
        "home_testimonials_title": _("Testimonials title"),
        "home_testimonials_subtitle": _("Testimonials introduction"),
        "about_eyebrow": _("About eyebrow"),
        "about_title": _("About title"),
        "about_subtitle": _("About introduction"),
        "about_history_title": _("History title"),
        "about_history": _("History"),
        "facility_title": _("Facility title"),
        "facility_description": _("Facility description"),
        "team_title": _("Team title"),
        "team_subtitle": _("Team introduction"),
        "contact_eyebrow": _("Contact eyebrow"),
        "contact_title": _("Contact title"),
        "contact_subtitle": _("Contact introduction"),
        "contact_visit_notice": _("Visit notice"),
        "pricing_eyebrow": _("Pricing eyebrow"),
        "pricing_title": _("Pricing title"),
        "pricing_subtitle": _("Pricing introduction"),
    }
    required_translations = tuple(translated_labels)

    class Meta:
        model = SiteSettings
        fields = (
            "company_name",
            "public_email",
            "primary_phone",
            "secondary_phone",
            "address_line_1",
            "address_line_2",
            "maps_embed_url",
            "directions_url",
            "weekday_hours_pt",
            "weekday_hours_en",
            "weekend_hours_pt",
            "weekend_hours_en",
            "home_services_eyebrow_pt",
            "home_services_eyebrow_en",
            "home_services_title_pt",
            "home_services_title_en",
            "home_services_subtitle_pt",
            "home_services_subtitle_en",
            "home_testimonials_eyebrow_pt",
            "home_testimonials_eyebrow_en",
            "home_testimonials_title_pt",
            "home_testimonials_title_en",
            "home_testimonials_subtitle_pt",
            "home_testimonials_subtitle_en",
            "about_eyebrow_pt",
            "about_eyebrow_en",
            "about_title_pt",
            "about_title_en",
            "about_subtitle_pt",
            "about_subtitle_en",
            "about_history_title_pt",
            "about_history_title_en",
            "about_history_pt",
            "about_history_en",
            "facility_title_pt",
            "facility_title_en",
            "facility_description_pt",
            "facility_description_en",
            "team_title_pt",
            "team_title_en",
            "team_subtitle_pt",
            "team_subtitle_en",
            "contact_eyebrow_pt",
            "contact_eyebrow_en",
            "contact_title_pt",
            "contact_title_en",
            "contact_subtitle_pt",
            "contact_subtitle_en",
            "contact_visit_notice_pt",
            "contact_visit_notice_en",
            "pricing_eyebrow_pt",
            "pricing_eyebrow_en",
            "pricing_title_pt",
            "pricing_title_en",
            "pricing_subtitle_pt",
            "pricing_subtitle_en",
        )
        widgets = {
            field_name: forms.Textarea(attrs={"rows": 3})
            for field_name in (
                "home_services_subtitle_pt",
                "home_services_subtitle_en",
                "home_testimonials_subtitle_pt",
                "home_testimonials_subtitle_en",
                "about_history_pt",
                "about_history_en",
                "contact_visit_notice_pt",
                "contact_visit_notice_en",
                "pricing_subtitle_pt",
                "pricing_subtitle_en",
            )
        }

    def clean_maps_embed_url(self):
        url = self.cleaned_data["maps_embed_url"]
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "google.com",
            "www.google.com",
            "maps.google.com",
        }:
            raise forms.ValidationError(_("Use an HTTPS Google Maps embed URL."))
        return url

    def clean_directions_url(self):
        return validate_https_url(self.cleaned_data["directions_url"])


class HomeSlideForm(ManagedImageContentForm):
    translated_labels = {
        "title": _("Title"),
        "description": _("Description"),
        "cta_text": _("Button text"),
        "image_alt": _("Image description"),
    }
    required_translations = ("title", "description", "image_alt")

    class Meta:
        model = HomeSlide
        fields = (
            "kind",
            "title_pt",
            "title_en",
            "description_pt",
            "description_en",
            "image",
            "image_alt_pt",
            "image_alt_en",
            "cta_text_pt",
            "cta_text_en",
            "cta_destination",
            "order",
            "is_active",
        )
        widgets = {
            "description_pt": forms.Textarea(attrs={"rows": 4}),
            "description_en": forms.Textarea(attrs={"rows": 4}),
        }

    def clean(self):
        cleaned_data = super().clean()
        has_destination = bool(cleaned_data.get("cta_destination"))
        has_any_label = bool(cleaned_data.get("cta_text_pt") or cleaned_data.get("cta_text_en"))
        if has_destination and not all(
            (cleaned_data.get("cta_text_pt"), cleaned_data.get("cta_text_en"))
        ):
            self.add_error("cta_text_pt", _("Add button text in both languages."))
        elif has_any_label and not has_destination:
            self.add_error("cta_destination", _("Choose where this button should lead."))
        is_active_hero = cleaned_data.get("kind") == HomeSlideKind.HERO and cleaned_data.get(
            "is_active"
        )
        another_active_hero_exists = (
            HomeSlide.objects.filter(kind=HomeSlideKind.HERO, is_active=True)
            .exclude(pk=self.instance.pk)
            .exists()
        )
        if not is_active_hero and not another_active_hero_exists:
            self.add_error(
                "is_active",
                _("Publish at least one main carousel slide so the homepage has a heading."),
            )
        return cleaned_data


class ServiceHighlightForm(LocalizedContentForm):
    translated_labels = {
        "title": _("Title"),
        "description": _("Description"),
        "features": _("Features, one per line"),
        "action_text": _("Button text"),
    }
    required_translations = tuple(translated_labels)

    class Meta:
        model = ServiceHighlight
        fields = (
            "icon",
            "title_pt",
            "title_en",
            "description_pt",
            "description_en",
            "features_pt",
            "features_en",
            "action_text_pt",
            "action_text_en",
            "catalog_type",
            "destination",
            "order",
            "is_active",
        )
        widgets = {
            "description_pt": forms.Textarea(attrs={"rows": 3}),
            "description_en": forms.Textarea(attrs={"rows": 3}),
            "features_pt": forms.Textarea(attrs={"rows": 5}),
            "features_en": forms.Textarea(attrs={"rows": 5}),
        }

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("catalog_type") and cleaned_data.get("destination"):
            self.add_error(
                "destination",
                _("Choose either a catalog type or another website page, not both."),
            )
        if (
            cleaned_data.get("is_active")
            and (cleaned_data.get("action_text_pt") or cleaned_data.get("action_text_en"))
            and not cleaned_data.get("catalog_type")
            and not cleaned_data.get("destination")
        ):
            self.add_error(
                "catalog_type",
                _("Choose where the button should lead."),
            )
        return cleaned_data


class TestimonialForm(ManagedImageContentForm):
    translated_labels = {
        "headline": _("Headline"),
        "body": _("Text"),
        "attribution": _("Attribution"),
        "image_alt": _("Image description"),
    }
    required_translations = ("headline", "body", "image_alt")

    class Meta:
        model = Testimonial
        fields = (
            "headline_pt",
            "headline_en",
            "body_pt",
            "body_en",
            "attribution_pt",
            "attribution_en",
            "image",
            "image_alt_pt",
            "image_alt_en",
            "order",
            "is_active",
        )
        widgets = {
            "body_pt": forms.Textarea(attrs={"rows": 4}),
            "body_en": forms.Textarea(attrs={"rows": 4}),
        }


class TeamMemberForm(ManagedImageContentForm):
    translated_labels = {
        "role": _("Role"),
        "description": _("Description"),
        "specialization": _("Specialization"),
        "image_alt": _("Image description"),
    }
    required_translations = tuple(translated_labels)

    class Meta:
        model = TeamMember
        fields = (
            "name",
            "role_pt",
            "role_en",
            "description_pt",
            "description_en",
            "specialization_pt",
            "specialization_en",
            "image",
            "image_alt_pt",
            "image_alt_en",
            "order",
            "is_active",
        )
        widgets = {
            "description_pt": forms.Textarea(attrs={"rows": 4}),
            "description_en": forms.Textarea(attrs={"rows": 4}),
        }


class PartnerForm(LocalizedContentForm):
    translated_labels = {"category": _("Category"), "tagline": _("Tagline")}
    required_translations = ("category",)

    class Meta:
        model = Partner
        fields = (
            "name",
            "category_pt",
            "category_en",
            "website_url",
            "symbol",
            "tagline_pt",
            "tagline_en",
            "order",
            "is_active",
        )

    def clean_website_url(self):
        return validate_https_url(self.cleaned_data["website_url"])


class SocialLinkForm(forms.ModelForm):
    class Meta:
        model = SocialLink
        fields = ("platform", "url", "order", "is_active")

    def clean_url(self):
        return validate_https_url(self.cleaned_data["url"])


class ContactSubjectForm(LocalizedContentForm):
    translated_labels = {"name": _("Name")}
    required_translations = ("name",)

    class Meta:
        model = ContactSubject
        fields = ("name_pt", "name_en", "order", "is_active")
