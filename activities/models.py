from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import CustomerProfile
from catalog.models import (
    AddOnAvailability,
    CatalogLevel,
    CatalogOffer,
    CatalogType,
    CatalogWorkflow,
)
from discounts.models import DiscountSnapshotMixin
from hotel.models import BookingDocumentStatus
from olhocao.models import ImmutableFinancialRecord
from pets.models import Pet


class OfferingMode(models.TextChoices):
    ONE_OFF = "one_off", _("One-off purchase")
    PACK = "pack", _("Pack")
    SUBSCRIPTION = "subscription", _("Subscription")


class EasypayFrequency(models.TextChoices):
    WEEKLY = "1W", _("Weekly")
    TWO_WEEKS = "2W", _("Every two weeks")
    MONTHLY = "1M", _("Monthly")
    TWO_MONTHS = "2M", _("Every two months")
    QUARTERLY = "3M", _("Quarterly")
    SIX_MONTHS = "6M", _("Every six months")
    YEARLY = "1Y", _("Yearly")


class ServiceOffering(models.Model):
    """Pack or subscription terms for one catalog offer."""

    catalog_offer = models.OneToOneField(
        CatalogOffer,
        on_delete=models.PROTECT,
        related_name="activity_offering",
    )
    mode = models.CharField(max_length=20, choices=OfferingMode.choices, db_index=True)
    included_sessions = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text=_("Number of sessions credited by a pack."),
    )
    sessions_per_week = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text=_("Weekly reservation limit for a subscription."),
    )
    validity_days = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text=_("Pack expiry in days after successful payment."),
    )
    billing_frequency = models.CharField(
        max_length=3,
        choices=EasypayFrequency.choices,
        blank=True,
    )
    cancellation_notice_hours = models.PositiveSmallIntegerField(default=24)
    is_published = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = (
            "catalog_offer__catalog_type__order",
            "catalog_offer__level__order",
            "mode",
            "catalog_offer__catalog_item__name",
        )

    def clean(self):
        errors = {}
        if (
            self.catalog_offer_id
            and self.catalog_offer.catalog_type.workflow == CatalogWorkflow.DATE_RANGE
        ):
            errors["catalog_offer"] = _("Hotel accommodation is purchased through a stay.")
        if self.is_published and self.catalog_offer_id and not self.catalog_offer.is_available:
            errors["catalog_offer"] = _("Only an active catalog offer can be published.")
        if self.mode == OfferingMode.ONE_OFF:
            if self.included_sessions:
                errors["included_sessions"] = _("A one-off purchase always grants one use.")
            if self.sessions_per_week:
                errors["sessions_per_week"] = _("A one-off purchase has no weekly limit.")
            if self.validity_days:
                errors["validity_days"] = _("A one-off purchase has no pack validity.")
            if self.billing_frequency:
                errors["billing_frequency"] = _("A one-off purchase does not recur.")
        elif self.mode == OfferingMode.PACK:
            if not self.included_sessions:
                errors["included_sessions"] = _("A pack must credit at least one session.")
            if not self.validity_days:
                errors["validity_days"] = _("A pack must have an expiry period.")
            if self.sessions_per_week:
                errors["sessions_per_week"] = _("A pack cannot have a weekly subscription limit.")
            if self.billing_frequency:
                errors["billing_frequency"] = _("A pack cannot have a billing frequency.")
        elif self.mode == OfferingMode.SUBSCRIPTION:
            if not self.sessions_per_week:
                errors["sessions_per_week"] = _("A subscription must have a weekly limit.")
            if not self.billing_frequency:
                errors["billing_frequency"] = _("A subscription needs a billing frequency.")
            if self.included_sessions:
                errors["included_sessions"] = _("A subscription does not credit pack sessions.")
            if self.validity_days:
                errors["validity_days"] = _("A subscription does not use pack validity.")
        if errors:
            raise ValidationError(errors)

    @property
    def is_available(self):
        return self.is_published and self.catalog_offer.is_available

    @property
    def catalog_item(self):
        return self.catalog_offer.catalog_item

    @property
    def catalog_type(self):
        return self.catalog_offer.catalog_type

    @property
    def catalog_level(self):
        return self.catalog_offer.level

    @property
    def price_eur(self):
        return self.catalog_item.unit_price_eur

    @property
    def fallback_image_static_path(self):
        if self.catalog_type.workflow == CatalogWorkflow.TIMED_SLOT:
            return "images/services/training.webp"
        return "images/services/park-play.webp"

    def __str__(self):
        return self.catalog_item.name


class EntitlementStatus(models.TextChoices):
    PENDING = "pending", _("Awaiting payment")
    ACTIVE = "active", _("Active")
    PAST_DUE = "past_due", _("Payment failed")
    CANCEL_AT_PERIOD_END = "cancel_at_period_end", _("Ends at period end")
    SUSPENDED = "suspended", _("Suspended")
    CANCELLED = "cancelled", _("Cancelled")
    EXPIRED = "expired", _("Expired")


SUBSCRIPTION_EXCLUSIVE_STATUSES = (
    EntitlementStatus.PENDING,
    EntitlementStatus.ACTIVE,
    EntitlementStatus.PAST_DUE,
    EntitlementStatus.CANCEL_AT_PERIOD_END,
    EntitlementStatus.SUSPENDED,
)


class ServiceEntitlement(DiscountSnapshotMixin, ImmutableFinancialRecord):
    """A paid pack or recurring service agreement for one dog."""

    customer = models.ForeignKey(
        CustomerProfile,
        on_delete=models.PROTECT,
        related_name="service_entitlements",
    )
    pet = models.ForeignKey(Pet, on_delete=models.PROTECT, related_name="service_entitlements")
    offering = models.ForeignKey(
        ServiceOffering,
        on_delete=models.PROTECT,
        related_name="entitlements",
    )
    source_activity_reservation = models.ForeignKey(
        "ActivityReservation",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="add_on_entitlements",
        help_text=_(
            "Activity reservation where this optional service was selected. "
            "The purchase remains valid even if that session is later changed."
        ),
    )
    catalog_type_reference = models.PositiveBigIntegerField(
        editable=False,
    )
    catalog_type_name_snapshot = models.CharField(max_length=120, editable=False)
    catalog_level_reference = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    catalog_level_name_snapshot = models.CharField(max_length=120, blank=True, editable=False)
    offering_mode_snapshot = models.CharField(
        max_length=20,
        choices=OfferingMode.choices,
        editable=False,
    )
    included_sessions_snapshot = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        editable=False,
    )
    sessions_per_week_snapshot = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        editable=False,
    )
    validity_days_snapshot = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        editable=False,
    )
    billing_frequency_snapshot = models.CharField(
        max_length=3,
        choices=EasypayFrequency.choices,
        blank=True,
        editable=False,
    )
    cancellation_notice_hours_snapshot = models.PositiveSmallIntegerField(
        editable=False,
    )
    status = models.CharField(
        max_length=30,
        choices=EntitlementStatus.choices,
        default=EntitlementStatus.PENDING,
        db_index=True,
    )
    item_name_snapshot = models.CharField(max_length=255)
    item_code_snapshot = models.CharField(max_length=100, blank=True)
    tax_code_snapshot = models.CharField(max_length=20, blank=True)
    unit_price_eur_snapshot = models.DecimalField(max_digits=12, decimal_places=2)
    currency_snapshot = models.CharField(max_length=3, default="EUR")
    merchant_reference = models.CharField(max_length=50, unique=True)
    provider_link_id = models.CharField(max_length=100, blank=True, db_index=True)
    provider_subscription_id = models.CharField(max_length=100, blank=True, db_index=True)
    provider_payment_id = models.CharField(max_length=100, blank=True)
    provider_capture_id = models.CharField(max_length=100, blank=True)
    payment_url = models.URLField(max_length=500, blank=True)
    qr_code_url = models.URLField(max_length=500, blank=True)
    payment_method = models.CharField(max_length=20, blank=True)
    payment_expires_at = models.DateTimeField(null=True, blank=True)
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    resume_scheduled_at = models.DateTimeField(null=True, blank=True)
    access_blocked_at = models.DateTimeField(null=True, blank=True)
    access_blocked_reason = models.CharField(max_length=500, blank=True)
    provider_synced_at = models.DateTimeField(null=True, blank=True)
    confirmation_sent_at = models.DateTimeField(null=True, blank=True)
    last_payment_error = models.TextField(blank=True)
    toconline_sale_document_id = models.CharField(max_length=100, blank=True)
    document_status = models.CharField(
        max_length=20,
        choices=BookingDocumentStatus.choices,
        default=BookingDocumentStatus.NOT_REQUIRED,
    )
    document_error = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_service_entitlements",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=("customer", "status"), name="activity_customer_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("customer", "pet", "offering"),
                condition=models.Q(status=EntitlementStatus.PENDING),
                name="one_pending_activity_purchase",
            ),
            models.UniqueConstraint(
                fields=("customer", "pet", "catalog_type_reference"),
                condition=models.Q(
                    offering_mode_snapshot=OfferingMode.SUBSCRIPTION,
                    status__in=SUBSCRIPTION_EXCLUSIVE_STATUSES,
                ),
                name="one_open_subscription_per_activity",
            ),
            models.UniqueConstraint(
                fields=("source_activity_reservation", "offering"),
                condition=models.Q(
                    source_activity_reservation__isnull=False,
                    status__in=SUBSCRIPTION_EXCLUSIVE_STATUSES,
                ),
                name="one_open_add_on_per_activity_reservation",
            ),
        ]

    def clean(self):
        errors = {}
        if self.pet_id and self.customer_id and self.pet.owner_id != self.customer_id:
            errors["pet"] = _("The dog does not belong to this customer.")
        if self.source_activity_reservation_id:
            source = self.source_activity_reservation
            if self.pet_id and source.pet_id != self.pet_id:
                errors["source_activity_reservation"] = _(
                    "The optional service must be for the dog in the activity reservation."
                )
            if self.customer_id and source.entitlement.customer_id != self.customer_id:
                errors["source_activity_reservation"] = _(
                    "The activity reservation belongs to a different customer."
                )
            if self.offering_id:
                offer = self.offering.catalog_offer
                if (
                    self.offering.mode != OfferingMode.ONE_OFF
                    or offer.catalog_type.workflow != CatalogWorkflow.FLEXIBLE
                ):
                    errors["offering"] = _(
                        "Only a flexible one-off service can be attached to an activity."
                    )
                level_filter = models.Q(target_level__isnull=True)
                if source.entitlement.catalog_level_reference:
                    level_filter |= models.Q(
                        target_level_id=source.entitlement.catalog_level_reference
                    )
                if not AddOnAvailability.objects.filter(
                    level_filter,
                    add_on=offer,
                    target_type_id=source.session.catalog_type_id,
                    is_active=True,
                ).exists():
                    errors["offering"] = _(
                        "This optional service is not available for the reserved activity."
                    )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Contract terms are frozen when purchased so later catalog configuration
        # changes cannot silently rewrite access already sold to a customer.
        if self._state.adding and self.offering_id:
            offering = self.offering
            self.catalog_type_reference = offering.catalog_offer.catalog_type_id
            self.catalog_type_name_snapshot = offering.catalog_type.name
            self.catalog_level_reference = offering.catalog_offer.level_id
            self.catalog_level_name_snapshot = (
                offering.catalog_level.name if offering.catalog_level else ""
            )
            self.offering_mode_snapshot = offering.mode
            self.included_sessions_snapshot = offering.included_sessions
            self.sessions_per_week_snapshot = offering.sessions_per_week
            self.validity_days_snapshot = offering.validity_days
            self.billing_frequency_snapshot = offering.billing_frequency
            self.cancellation_notice_hours_snapshot = offering.cancellation_notice_hours
            self.item_code_snapshot = offering.catalog_item.item_code
            self.tax_code_snapshot = offering.catalog_item.tax_code
        super().save(*args, **kwargs)

    @property
    def credit_balance(self):
        if hasattr(self, "credit_balance_value"):
            return self.credit_balance_value
        return self.credit_entries.aggregate(total=Sum("delta"))["total"] or 0

    @property
    def total_price_eur(self):
        return max(
            self.unit_price_eur_snapshot - self.discount_amount_eur_snapshot,
            Decimal("0.00"),
        )

    @property
    def has_access(self):
        now = timezone.now()
        if self.access_blocked_at:
            return False
        if self.status not in {
            EntitlementStatus.ACTIVE,
            EntitlementStatus.CANCEL_AT_PERIOD_END,
            EntitlementStatus.PAST_DUE,
        }:
            return False
        if self.status == EntitlementStatus.PAST_DUE and not self.current_period_end:
            return False
        if self.current_period_end and self.current_period_end <= now:
            return False
        if self.offering_mode_snapshot in {OfferingMode.ONE_OFF, OfferingMode.PACK}:
            return self.credit_balance > 0
        return True

    def __str__(self):
        return f"{self.item_name_snapshot} · {self.pet.name}"


class LedgerReason(models.TextChoices):
    PURCHASE = "purchase", _("Pack purchase")
    RESERVATION = "reservation", _("Reservation")
    CANCELLATION = "cancellation", _("Reservation cancellation")
    ADJUSTMENT = "adjustment", _("Manual adjustment")


class CreditLedgerEntry(ImmutableFinancialRecord):
    entitlement = models.ForeignKey(
        ServiceEntitlement,
        on_delete=models.PROTECT,
        related_name="credit_entries",
    )
    delta = models.SmallIntegerField()
    reason = models.CharField(max_length=20, choices=LedgerReason.choices)
    idempotency_key = models.CharField(max_length=100, unique=True)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="activity_credit_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")


class WeeklyUsageAdjustment(ImmutableFinancialRecord):
    """Append-only extra weekly allowance granted by staff.

    The contracted weekly allowance never rolls over. This ledger only changes
    one explicitly identified week and therefore cannot accidentally create a
    permanent contract change.
    """

    entitlement = models.ForeignKey(
        ServiceEntitlement,
        on_delete=models.PROTECT,
        related_name="weekly_usage_adjustments",
    )
    week_start = models.DateField(help_text=_("Monday of the week receiving extra uses."))
    extra_uses = models.PositiveSmallIntegerField()
    reason = models.CharField(max_length=500)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="activity_weekly_usage_adjustments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-week_start", "-created_at", "-id")
        constraints = (
            models.CheckConstraint(
                condition=models.Q(extra_uses__gte=1),
                name="weekly_usage_adjustment_is_positive",
            ),
        )

    def clean(self):
        if self.week_start and self.week_start.weekday() != 0:
            raise ValidationError({"week_start": _("Choose the Monday of the adjusted week.")})
        if (
            self.entitlement_id
            and self.entitlement.offering_mode_snapshot != OfferingMode.SUBSCRIPTION
        ):
            raise ValidationError(
                {"entitlement": _("Weekly allowance adjustments only apply to subscriptions.")}
            )


def trainer_image_upload_to(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    return f"activities/trainers/{instance.pk or 'new'}/{timezone.now():%Y%m%d%H%M%S}.{extension}"


def validate_trainer_image_size(image):
    if image.size > settings.CATALOG_IMAGE_MAX_UPLOAD_BYTES:
        raise ValidationError(_("The trainer image is too large."))


class Trainer(models.Model):
    """Operational trainer record, optionally linked to a staff login."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="trainer_profile",
    )
    name = models.CharField(max_length=150)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    biography = models.TextField(blank=True)
    image = models.ImageField(
        upload_to=trainer_image_upload_to,
        blank=True,
        validators=(
            FileExtensionValidator(allowed_extensions=("jpeg", "jpg", "png", "webp")),
            validate_trainer_image_size,
        ),
    )
    image_alt = models.CharField(max_length=180, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "id")

    def clean(self):
        if self.user_id and not self.user.is_staff:
            raise ValidationError({"user": _("A linked trainer account must be a staff account.")})

    def __str__(self):
        return self.name


class SessionSeries(models.Model):
    """A weekly rule that materializes auditable concrete calendar sessions."""

    catalog_type = models.ForeignKey(
        CatalogType,
        on_delete=models.PROTECT,
        related_name="session_series",
    )
    title = models.CharField(max_length=150)
    starts_on = models.DateField()
    ends_on = models.DateField()
    weekdays = models.JSONField(
        default=list,
        help_text=_("ISO weekdays from 1 (Monday) to 7 (Sunday)."),
    )
    starts_at_time = models.TimeField()
    ends_at_time = models.TimeField()
    capacity_override = models.PositiveSmallIntegerField(null=True, blank=True)
    eligible_levels = models.ManyToManyField(
        CatalogLevel,
        blank=True,
        related_name="session_series",
    )
    trainers = models.ManyToManyField(Trainer, blank=True, related_name="session_series")
    skip_closed_days = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True, db_index=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_activity_session_series",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("starts_on", "starts_at_time", "id")

    @property
    def effective_capacity(self):
        return self.capacity_override or self.catalog_type.default_capacity

    def clean(self):
        errors = {}
        if self.catalog_type_id and not self.catalog_type.is_scheduled:
            errors["catalog_type"] = _("Choose a full-day or timed-session catalog type.")
        if self.ends_on and self.starts_on and self.ends_on < self.starts_on:
            errors["ends_on"] = _("The final date must not be before the first date.")
        if self.ends_at_time and self.starts_at_time and self.ends_at_time <= self.starts_at_time:
            errors["ends_at_time"] = _("End time must be after start time.")
        weekdays = self.weekdays or []
        if not weekdays or any(not isinstance(day, int) or day < 1 or day > 7 for day in weekdays):
            errors["weekdays"] = _("Choose at least one valid weekday.")
        if len(set(weekdays)) != len(weekdays):
            errors["weekdays"] = _("Each weekday can only be selected once.")
        if not self.effective_capacity:
            errors["capacity_override"] = _("Configure a capacity for this schedule.")
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.catalog_type.name} · {self.title}"


class ClosureSource(models.TextChoices):
    MANUAL = "manual", _("Manual closure")
    MUNICIPAL_HOLIDAY = "municipal_holiday", _("Municipal or optional holiday")


class OperationalClosure(models.Model):
    """Blocks new availability without silently rewriting existing purchases."""

    name = models.CharField(max_length=150)
    source = models.CharField(
        max_length=24,
        choices=ClosureSource.choices,
        default=ClosureSource.MANUAL,
    )
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField(db_index=True)
    all_operational_services = models.BooleanField(
        default=False,
        help_text=_(
            "Apply to every non-accommodation service. Date-range accommodation "
            "always remains available online."
        ),
    )
    catalog_types = models.ManyToManyField(CatalogType, blank=True, related_name="closures")
    reason = models.CharField(max_length=500)
    customer_message = models.CharField(max_length=240, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_operational_closures",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("starts_at", "id")
        indexes = (
            models.Index(
                fields=("is_active", "starts_at", "ends_at"),
                name="closure_active_range_idx",
            ),
        )
        constraints = (
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="operational_closure_has_positive_duration",
            ),
        )

    def clean(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": _("End time must be after start time.")})

    def __str__(self):
        return self.name


class ActivitySession(models.Model):
    title = models.CharField(max_length=150)
    catalog_type = models.ForeignKey(
        CatalogType,
        on_delete=models.PROTECT,
        related_name="sessions",
    )
    eligible_levels = models.ManyToManyField(
        CatalogLevel,
        blank=True,
        related_name="sessions",
        help_text=_("Leave empty to accept every active level in this catalog type."),
    )
    trainers = models.ManyToManyField(Trainer, blank=True, related_name="sessions")
    source_series = models.ForeignKey(
        SessionSeries,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="sessions",
    )
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField()
    capacity = models.PositiveSmallIntegerField(default=10)
    is_cancelled = models.BooleanField(default=False, db_index=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_activity_sessions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("starts_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="activity_session_has_positive_duration",
            ),
            models.CheckConstraint(
                condition=models.Q(capacity__gte=1),
                name="activity_session_has_capacity",
            ),
            models.UniqueConstraint(
                fields=("source_series", "starts_at"),
                condition=models.Q(source_series__isnull=False),
                name="unique_materialized_activity_session",
            ),
        ]

    @property
    def reserved_places(self):
        if hasattr(self, "reserved_places_value"):
            return self.reserved_places_value
        return self.reservations.filter(status__in=RESERVED_PLACE_STATUSES).count()

    @property
    def available_places(self):
        return max(self.capacity - self.reserved_places, 0)

    def clean(self):
        errors = {}
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            errors["ends_at"] = _("End time must be after start time.")
        if self.catalog_type_id and not self.catalog_type.is_scheduled:
            errors["catalog_type"] = _("Choose a full-day or timed-session catalog type.")
        if self.source_series_id and self.source_series.catalog_type_id != self.catalog_type_id:
            errors["source_series"] = _("The schedule rule belongs to another catalog type.")
        if errors:
            raise ValidationError(errors)

    @property
    def is_full_day(self):
        return self.catalog_type.workflow == CatalogWorkflow.FULL_DAY_SLOT

    def __str__(self):
        return f"{self.title} · {timezone.localtime(self.starts_at):%d/%m/%Y %H:%M}"


class ActivitySessionEvent(ImmutableFinancialRecord):
    """Append-only audit event for manual scheduling decisions."""

    session = models.ForeignKey(
        ActivitySession,
        on_delete=models.PROTECT,
        related_name="events",
    )
    action = models.CharField(max_length=30)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="activity_session_events",
    )
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")


class ReservationStatus(models.TextChoices):
    BOOKED = "booked", _("Booked")
    ATTENDED = "attended", _("Attended")
    CANCELLED = "cancelled", _("Cancelled")
    LATE_CANCELLED = "late_cancelled", _("Cancelled after deadline")
    NO_SHOW = "no_show", _("No-show")


RESERVED_PLACE_STATUSES = (ReservationStatus.BOOKED, ReservationStatus.ATTENDED)
CONSUMED_WEEKLY_USAGE_STATUSES = (
    ReservationStatus.BOOKED,
    ReservationStatus.ATTENDED,
    ReservationStatus.LATE_CANCELLED,
    ReservationStatus.NO_SHOW,
)


class ActivityReservation(models.Model):
    session = models.ForeignKey(
        ActivitySession,
        on_delete=models.PROTECT,
        related_name="reservations",
    )
    entitlement = models.ForeignKey(
        ServiceEntitlement,
        on_delete=models.PROTECT,
        related_name="reservations",
    )
    pet = models.ForeignKey(Pet, on_delete=models.PROTECT, related_name="activity_reservations")
    status = models.CharField(
        max_length=20,
        choices=ReservationStatus.choices,
        default=ReservationStatus.BOOKED,
        db_index=True,
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_activity_reservations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.CharField(max_length=500, blank=True)
    cancellation_policy_overridden = models.BooleanField(default=False)

    class Meta:
        ordering = ("session__starts_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("session", "pet"),
                condition=models.Q(status__in=RESERVED_PLACE_STATUSES),
                name="one_active_activity_reservation_per_pet",
            )
        ]

    def clean(self):
        errors = {}
        if self.entitlement_id and self.pet_id and self.entitlement.pet_id != self.pet_id:
            errors["pet"] = _("The reservation dog must match the purchased service.")
        if self.session_id and self.entitlement_id:
            if self.session.catalog_type_id != self.entitlement.catalog_type_reference:
                errors["session"] = _("This service does not grant access to that activity.")
            eligible_level_ids = set(self.session.eligible_levels.values_list("id", flat=True))
            if (
                eligible_level_ids
                and self.entitlement.catalog_level_reference not in eligible_level_ids
            ):
                errors["session"] = _("This service level cannot book that session.")
        if errors:
            raise ValidationError(errors)


class ActivityReservationEvent(models.Model):
    reservation = models.ForeignKey(
        ActivityReservation,
        on_delete=models.PROTECT,
        related_name="events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="activity_reservation_events",
    )
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, choices=ReservationStatus.choices)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")


class ServiceFulfilmentStatus(models.TextChoices):
    AWAITING_PAYMENT = "awaiting_payment", _("Awaiting payment")
    REQUESTED = "requested", _("Requested")
    SCHEDULED = "scheduled", _("Scheduled")
    COMPLETED = "completed", _("Completed")
    CANCELLED = "cancelled", _("Cancelled")


class ServiceFulfilment(models.Model):
    """Operational delivery of a flexible one-off service.

    A grooming request can come from an independently purchased entitlement or
    from a service line inside a hotel stay. It can carry an exact preference,
    a date-only preference, or no requested date at all. Financial records stay
    on their original purchase; this record only tracks delivery.
    """

    entitlement = models.ForeignKey(
        ServiceEntitlement,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="fulfilments",
    )
    booking_service = models.ForeignKey(
        "hotel.BookingService",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="fulfilments",
    )
    source_unit_number = models.PositiveSmallIntegerField(
        default=1,
        help_text=_("Unit number inside a multi-quantity hotel service line."),
    )
    pet = models.ForeignKey(Pet, on_delete=models.PROTECT, related_name="service_fulfilments")
    status = models.CharField(
        max_length=20,
        choices=ServiceFulfilmentStatus.choices,
        default=ServiceFulfilmentStatus.REQUESTED,
        db_index=True,
    )
    preferred_date = models.DateField(null=True, blank=True)
    preferred_time = models.TimeField(null=True, blank=True)
    scheduled_starts_at = models.DateTimeField(null=True, blank=True, db_index=True)
    scheduled_ends_at = models.DateTimeField(null=True, blank=True)
    usage_week_start = models.DateField(
        null=True,
        blank=True,
        editable=False,
        help_text=_("Monday whose non-transferable subscription allowance is used."),
    )
    consumes_entitlement_use = models.BooleanField(
        default=False,
        help_text=_(
            "Whether this request consumes a pack credit or weekly subscription allowance."
        ),
    )
    customer_notes = models.TextField(blank=True)
    internal_notes = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="completed_service_fulfilments",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_service_fulfilments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.CharField(max_length=500, blank=True)
    cancellation_policy_overridden = models.BooleanField(default=False)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = (
            models.CheckConstraint(
                condition=(
                    models.Q(entitlement__isnull=False, booking_service__isnull=True)
                    | models.Q(entitlement__isnull=True, booking_service__isnull=False)
                ),
                name="fulfilment_has_exactly_one_purchase_source",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(scheduled_ends_at__isnull=True)
                    | models.Q(scheduled_starts_at__isnull=False)
                ),
                name="fulfilment_end_requires_start",
            ),
            models.UniqueConstraint(
                fields=("booking_service", "source_unit_number"),
                condition=models.Q(booking_service__isnull=False),
                name="unique_fulfilment_unit_per_booking_service",
            ),
            models.CheckConstraint(
                condition=models.Q(source_unit_number__gte=1),
                name="fulfilment_source_unit_is_positive",
            ),
        )

    @property
    def catalog_offer(self):
        if self.entitlement_id:
            return self.entitlement.offering.catalog_offer
        return self.booking_service.catalog_offer

    @property
    def customer(self):
        if self.entitlement_id:
            return self.entitlement.customer
        return self.booking_service.stay.booking.customer

    @property
    def source_label(self):
        if self.entitlement_id:
            return self.entitlement.item_name_snapshot
        return self.booking_service.item_name_snapshot

    def clean(self):
        errors = {}
        if bool(self.entitlement_id) == bool(self.booking_service_id):
            errors["entitlement"] = _("Choose exactly one purchase source.")
        if self.entitlement_id and self.pet_id != self.entitlement.pet_id:
            errors["pet"] = _("The dog must match the purchased service.")
        if self.booking_service_id:
            if self.pet_id != self.booking_service.pet_id:
                errors["pet"] = _("The dog must match the hotel service line.")
            if self.preferred_date and not (
                self.booking_service.stay.start_date
                <= self.preferred_date
                < self.booking_service.stay.end_date
            ):
                errors["preferred_date"] = _("Choose a date during the hotel stay.")
        if self.preferred_time and not self.preferred_date:
            errors["preferred_time"] = _("Choose a preferred date before choosing a time.")
        if (
            self.scheduled_starts_at
            and self.scheduled_ends_at
            and self.scheduled_ends_at <= self.scheduled_starts_at
        ):
            errors["scheduled_ends_at"] = _("The end time must be after the start time.")
        if self.completed_at and self.status != ServiceFulfilmentStatus.COMPLETED:
            errors["completed_at"] = _("Only a completed service can have a completion time.")
        if self.cancelled_at and self.status != ServiceFulfilmentStatus.CANCELLED:
            errors["cancelled_at"] = _("Only a cancelled service can have a cancellation time.")
        if errors:
            raise ValidationError(errors)


class ServiceFulfilmentEvent(ImmutableFinancialRecord):
    fulfilment = models.ForeignKey(
        ServiceFulfilment,
        on_delete=models.PROTECT,
        related_name="events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="service_fulfilment_events",
    )
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, choices=ServiceFulfilmentStatus.choices)
    reason = models.CharField(max_length=500)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")


class SubscriptionCycleStatus(models.TextChoices):
    PAID = "paid", _("Paid")
    FAILED = "failed", _("Failed")
    DISPUTED = "disputed", _("Disputed")


class RefundStatus(models.TextChoices):
    NOT_REQUESTED = "not_requested", _("Not requested")
    PENDING = "pending", _("Refund pending")
    REFUNDED = "refunded", _("Refunded")
    FAILED = "failed", _("Refund failed")


class EntitlementRefund(ImmutableFinancialRecord):
    """Append-only refund request for a one-off purchase or pack.

    The original entitlement, capture and fiscal document are retained.  Each
    partial refund gets its own provider and corrective-document references.
    """

    entitlement = models.ForeignKey(
        ServiceEntitlement,
        on_delete=models.PROTECT,
        related_name="refunds",
    )
    amount_eur = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(
        max_length=20,
        choices=RefundStatus.choices,
        default=RefundStatus.PENDING,
        db_index=True,
    )
    reason = models.CharField(max_length=500)
    revoke_remaining_access = models.BooleanField(default=False)
    refund_reference = models.CharField(max_length=100, unique=True)
    provider_refund_id = models.CharField(max_length=100, blank=True, db_index=True)
    provider_error = models.TextField(blank=True)
    toconline_amend_document_id = models.CharField(max_length=100, blank=True)
    document_error = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="requested_entitlement_refunds",
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    refunded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-requested_at", "-id")
        constraints = (
            models.CheckConstraint(
                condition=models.Q(amount_eur__gt=0),
                name="entitlement_refund_amount_is_positive",
            ),
        )


class SubscriptionCycle(ImmutableFinancialRecord):
    entitlement = models.ForeignKey(
        ServiceEntitlement,
        on_delete=models.PROTECT,
        related_name="subscription_cycles",
    )
    provider_cycle_id = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=20, choices=SubscriptionCycleStatus.choices)
    amount_eur = models.DecimalField(max_digits=12, decimal_places=2)
    provider_capture_id = models.CharField(max_length=100, blank=True, db_index=True)
    payment_method = models.CharField(max_length=20, blank=True)
    period_start = models.DateTimeField(null=True, blank=True)
    period_end = models.DateTimeField(null=True, blank=True)
    toconline_sale_document_id = models.CharField(max_length=100, blank=True)
    document_status = models.CharField(
        max_length=20,
        choices=BookingDocumentStatus.choices,
        default=BookingDocumentStatus.NOT_REQUIRED,
    )
    document_error = models.TextField(blank=True)
    refund_status = models.CharField(
        max_length=20,
        choices=RefundStatus.choices,
        default=RefundStatus.NOT_REQUESTED,
    )
    refund_amount_eur = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    refund_reference = models.CharField(max_length=100, blank=True, unique=True, null=True)
    provider_refund_id = models.CharField(max_length=100, blank=True, db_index=True)
    refund_error = models.TextField(blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    toconline_amend_document_id = models.CharField(max_length=100, blank=True)
    document_notification_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")


class SubscriptionManagementAction(models.TextChoices):
    SUSPENDED = "suspended", _("Suspended")
    RESUME_SCHEDULED = "resume_scheduled", _("Resume scheduled")
    CANCELLATION_SCHEDULED = "cancellation_scheduled", _("Cancellation scheduled")
    TERMINATED = "terminated", _("Terminated immediately")
    ACCESS_BLOCKED = "access_blocked", _("Access blocked")
    ACCESS_RESTORED = "access_restored", _("Access restored")
    PROVIDER_SYNCED = "provider_synced", _("Provider synchronized")
    DOCUMENT_RETRIED = "document_retried", _("Document issuance retried")
    REFUND_REQUESTED = "refund_requested", _("Refund requested")
    PAYMENT_DISPUTED = "payment_disputed", _("Payment disputed")


class SubscriptionManagementEvent(ImmutableFinancialRecord):
    """Append-only audit trail for administrative subscription operations."""

    entitlement = models.ForeignKey(
        ServiceEntitlement,
        on_delete=models.PROTECT,
        related_name="management_events",
    )
    action = models.CharField(max_length=40, choices=SubscriptionManagementAction.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="subscription_management_events",
    )
    from_status = models.CharField(max_length=30, choices=EntitlementStatus.choices)
    to_status = models.CharField(max_length=30, choices=EntitlementStatus.choices)
    reason = models.CharField(max_length=500)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")


class ActivityPaymentEvent(ImmutableFinancialRecord):
    entitlement = models.ForeignKey(
        ServiceEntitlement,
        on_delete=models.PROTECT,
        related_name="payment_events",
    )
    fingerprint = models.CharField(max_length=64, unique=True)
    event_type = models.CharField(max_length=50)
    provider_event_id = models.CharField(max_length=100, blank=True)
    provider_status = models.CharField(max_length=50, blank=True)
    details = models.JSONField(default=dict, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-received_at", "-id")
