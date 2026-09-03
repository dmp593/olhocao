import os
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import CustomerProfile
from discounts.models import DiscountSnapshotMixin
from olhocao.models import ImmutableFinancialRecord
from pets.models import Pet


class CatalogItemType(models.TextChoices):
    SERVICE = "Service", _("Service")
    PRODUCT = "Product", _("Product")


class CatalogItemOrigin(models.TextChoices):
    WEBSITE = "website", _("Managed by website")
    TOCONLINE = "toconline", _("Imported from TOConline")


class CatalogSyncStatus(models.TextChoices):
    PENDING = "pending", _("Waiting to publish")
    SYNCED = "synced", _("Synchronized")
    DIVERGED = "diverged", _("TOConline differs")
    FAILED = "failed", _("Synchronization failed")


class CatalogBookingKind(models.TextChoices):
    OTHER = "other", _("Not used in bookings")
    STAY = "stay", _("Accommodation")
    EXTRA = "extra", _("Booking extra")


CATALOG_IMAGE_FALLBACKS = {
    CatalogBookingKind.STAY: "images/services/hotel-stay.webp",
    CatalogBookingKind.EXTRA: "images/services/training.webp",
    CatalogBookingKind.OTHER: "images/services/enrichment.webp",
}


def catalog_image_upload_to(instance, filename):
    _, extension = os.path.splitext(filename)
    item_identifier = instance.item_code or instance.toconline_id or "pending"
    return (
        f"catalog/{instance.toconline_type.lower()}/{item_identifier}/"
        f"{uuid.uuid4().hex}{extension.lower()}"
    )


def validate_catalog_image_size(image):
    if image.size > settings.CATALOG_IMAGE_MAX_UPLOAD_BYTES:
        raise ValidationError(_("The catalog image is too large."))


class CatalogItem(models.Model):
    """Website catalog item with a stable projection in TOConline.

    New items are authored here and published to TOConline. Older ERP-authored
    items remain explicit imports until an administrator creates a managed
    successor. Fiscal versions are preserved once referenced by transactions.
    """

    toconline_id = models.CharField(max_length=100, null=True, blank=True)
    toconline_type = models.CharField(
        max_length=20,
        choices=CatalogItemType.choices,
    )
    item_code = models.CharField(max_length=100, blank=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    family_id = models.CharField(max_length=100, blank=True)
    family_name = models.CharField(max_length=255, blank=True)
    unit_price_eur = models.DecimalField(max_digits=12, decimal_places=2)
    tax_code = models.CharField(max_length=20, blank=True)
    currency = models.CharField(max_length=3, default="EUR")
    is_active = models.BooleanField(default=True, db_index=True)
    is_public = models.BooleanField(
        default=False,
        db_index=True,
        help_text=_("Show this item in the public price list."),
    )
    is_configured = models.BooleanField(
        default=False,
        db_index=True,
        help_text=_("The website role and presentation have been reviewed."),
    )
    booking_kind = models.CharField(
        max_length=20,
        choices=CatalogBookingKind.choices,
        default=CatalogBookingKind.OTHER,
        db_index=True,
        help_text=_(
            "Choose whether this catalog item is accommodation, an optional "
            "booking extra, or only displayed in the public price list."
        ),
    )
    image = models.ImageField(
        upload_to=catalog_image_upload_to,
        blank=True,
        validators=[
            FileExtensionValidator(allowed_extensions=("gif", "jpeg", "jpg", "png", "webp")),
            validate_catalog_image_size,
        ],
    )
    last_seen_at = models.DateTimeField(null=True, blank=True)
    remote_deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    origin = models.CharField(
        max_length=20,
        choices=CatalogItemOrigin.choices,
        default=CatalogItemOrigin.TOCONLINE,
        db_index=True,
    )
    sync_status = models.CharField(
        max_length=20,
        choices=CatalogSyncStatus.choices,
        default=CatalogSyncStatus.SYNCED,
        db_index=True,
    )
    sync_error = models.TextField(blank=True)
    remote_fingerprint = models.CharField(max_length=64, blank=True)
    sync_attempted_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)
    supersedes = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="newer_versions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("family_name", "name", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("toconline_type", "toconline_id"),
                name="unique_toconline_catalog_item",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price_eur__gte=0),
                name="catalog_price_is_not_negative",
            ),
            models.UniqueConstraint(
                fields=("toconline_type", "item_code"),
                condition=~models.Q(item_code=""),
                name="unique_catalog_item_code_per_type",
            ),
        ]
        indexes = [
            models.Index(
                fields=("booking_kind", "is_active", "remote_deleted_at"),
                name="catalog_booking_avail_idx",
            )
        ]

    @property
    def is_available(self):
        if self.origin == CatalogItemOrigin.WEBSITE:
            return self.is_active
        return self.is_active and self.remote_deleted_at is None and bool(self.toconline_id)

    @property
    def is_site_managed(self):
        return self.origin == CatalogItemOrigin.WEBSITE

    @property
    def fallback_image_static_path(self):
        """Return deterministic artwork until an item-specific image is uploaded."""
        activity_offering = getattr(self, "activity_offering", None)
        if activity_offering:
            return activity_offering.fallback_image_static_path
        return CATALOG_IMAGE_FALLBACKS[self.booking_kind]

    def __str__(self):
        return f"{self.item_code} - {self.name}" if self.item_code else self.name


class BookingStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    CANCELLED = "cancelled", _("Cancelled")
    PAID = "paid", _("Paid")
    COMPLIMENTARY = "complimentary", _("Complimentary")
    PAYMENT_DISPUTED = "payment_disputed", _("Payment disputed")


CONFIRMED_BOOKING_STATUSES = (BookingStatus.PAID, BookingStatus.COMPLIMENTARY)


class BookingDocumentStatus(models.TextChoices):
    NOT_REQUIRED = "not_required", _("Not required")
    PENDING = "pending", _("Pending")
    ISSUED = "issued", _("Issued")
    FAILED = "failed", _("Failed")


class BookingEventType(models.TextChoices):
    CREATED = "created", _("Created")
    RESCHEDULED = "rescheduled", _("Rescheduled")
    CANCELLED = "cancelled", _("Cancelled")
    PAYMENT_CONFIRMED = "payment_confirmed", _("Payment confirmed")
    PAYMENT_RECEIVED_AFTER_CANCELLATION = (
        "payment_after_cancellation",
        _("Payment received after cancellation"),
    )
    DUPLICATE_PAYMENT_RECEIVED = (
        "duplicate_payment_received",
        _("Duplicate payment received"),
    )
    PAYMENT_RECEIVED_AFTER_PRICE_CHANGE = (
        "payment_after_price_change",
        _("Payment received after price change"),
    )
    PAYMENT_RECEIVED_AFTER_SERVICE_START = (
        "payment_after_service_start",
        _("Payment received after service start"),
    )
    PRICE_ADJUSTED = "price_adjusted", _("Price adjusted")
    COMPLIMENTARY_CONFIRMED = "complimentary_confirmed", _("Complimentary booking confirmed")
    REFUND_REQUESTED = "refund_requested", _("Refund requested")
    REFUND_CONFIRMED = "refund_confirmed", _("Refund confirmed")
    REFUND_FAILED = "refund_failed", _("Refund failed")
    PAYMENT_DISPUTED = "payment_disputed", _("Payment disputed")
    DOCUMENT_ISSUED = "document_issued", _("Document issued")


class Booking(DiscountSnapshotMixin, models.Model):
    customer = models.ForeignKey(
        CustomerProfile,
        on_delete=models.PROTECT,
        related_name="bookings",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_bookings",
    )

    status = models.CharField(
        max_length=20, choices=BookingStatus.choices, default=BookingStatus.PENDING
    )

    toconline_sale_document_id = models.CharField(max_length=100, blank=True)

    toconline_amend_document_id = models.CharField(max_length=100, blank=True)

    document_status = models.CharField(
        max_length=20,
        choices=BookingDocumentStatus.choices,
        default=BookingDocumentStatus.NOT_REQUIRED,
    )
    document_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    notes = models.TextField(blank=True)

    @property
    def gross_total_price_eur(self):
        return sum(
            (stay.gross_total_price_eur for stay in self.stays.all()),
            Decimal("0.00"),
        )

    @property
    def total_price_eur(self):
        return max(
            self.gross_total_price_eur - self.discount_amount_eur_snapshot,
            Decimal("0.00"),
        )

    @property
    def gross_accommodation_price_eur(self):
        return sum(
            (stay.unit_price_eur * stay.duration_days for stay in self.stays.all()),
            Decimal("0.00"),
        )

    @property
    def accommodation_price_eur(self):
        return sum(
            (stay.accommodation_total_price_eur for stay in self.stays.all()),
            Decimal("0.00"),
        )

    @property
    def all_pets(self):
        return ", ".join([stay.pet.name for stay in self.stays.all()])

    @property
    def earliest_start_date(self):
        if not self.stays.exists():
            return None
        return min(stay.start_date for stay in self.stays.all())

    @property
    def latest_end_date(self):
        if not self.stays.exists():
            return None
        return max(stay.end_date for stay in self.stays.all())

    @property
    def is_past(self):
        now = timezone.localdate()
        return any(stay.end_date < now for stay in self.stays.all())

    @property
    def is_active(self):
        now = timezone.localdate()
        return self.status in CONFIRMED_BOOKING_STATUSES and any(
            stay.start_date <= now < stay.end_date for stay in self.stays.all()
        )

    @property
    def is_upcoming(self):
        now = timezone.localdate()
        stays = tuple(self.stays.all())
        return (
            self.status in CONFIRMED_BOOKING_STATUSES
            and bool(stays)
            and all(stay.start_date > now for stay in stays)
        )

    @property
    def can_modify(self):
        if self.earliest_start_date is None:
            return False

        days_until_start = self.earliest_start_date - timezone.localdate()
        return days_until_start.days >= 7 and self.status not in {
            BookingStatus.CANCELLED,
            BookingStatus.PAYMENT_DISPUTED,
        }

    @property
    def can_cancel(self):
        if self.earliest_start_date is None:
            return False

        days_until_start = self.earliest_start_date - timezone.localdate()
        return days_until_start.days >= 7 and self.status not in {
            BookingStatus.CANCELLED,
            BookingStatus.PAYMENT_DISPUTED,
        }

    def __str__(self):
        return _("Booking") + f" #{self.pk}"


class BookingEvent(models.Model):
    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name="events",
    )
    event_type = models.CharField(max_length=40, choices=BookingEventType.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="booking_events",
    )
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [models.Index(fields=("booking", "event_type"), name="booking_event_type_idx")]


class BookingPriceAdjustment(ImmutableFinancialRecord):
    """Append-only audit record for a staff-authored final-price change."""

    booking = models.ForeignKey(
        Booking,
        on_delete=models.PROTECT,
        related_name="price_adjustments",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="booking_price_adjustments",
    )
    gross_amount_eur = models.DecimalField(max_digits=12, decimal_places=2)
    previous_total_amount_eur = models.DecimalField(max_digits=12, decimal_places=2)
    new_total_amount_eur = models.DecimalField(max_digits=12, decimal_places=2)
    discount_amount_eur = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(gross_amount_eur__gte=0),
                name="booking_adjustment_gross_not_negative",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(previous_total_amount_eur__gte=0)
                    & models.Q(previous_total_amount_eur__lte=models.F("gross_amount_eur"))
                ),
                name="booking_adjustment_previous_within_gross",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(new_total_amount_eur__gte=0)
                    & models.Q(new_total_amount_eur__lte=models.F("gross_amount_eur"))
                ),
                name="booking_adjustment_new_within_gross",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    discount_amount_eur=(
                        models.F("gross_amount_eur") - models.F("new_total_amount_eur")
                    )
                ),
                name="booking_adjustment_discount_matches_total",
            ),
        ]


class PaymentProvider(models.TextChoices):
    EASYPAY = "easypay", "Easypay"


class PaymentStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    PAID = "paid", _("Paid")
    FAILED = "failed", _("Failed")
    EXPIRED = "expired", _("Expired")
    CANCELLED = "cancelled", _("Cancelled")
    REFUND_PENDING = "refund_pending", _("Refund pending")
    REFUNDED = "refunded", _("Refunded")
    REFUND_FAILED = "refund_failed", _("Refund failed")
    DISPUTED = "disputed", _("Disputed")


class PaymentAttempt(ImmutableFinancialRecord):
    """One hosted payment-link attempt for a booking."""

    booking = models.ForeignKey(
        Booking,
        on_delete=models.PROTECT,
        related_name="payment_attempts",
    )
    provider = models.CharField(
        max_length=20,
        choices=PaymentProvider.choices,
        default=PaymentProvider.EASYPAY,
    )
    merchant_reference = models.CharField(max_length=50, unique=True)
    provider_link_id = models.CharField(max_length=100, blank=True, db_index=True)
    provider_payment_id = models.CharField(max_length=100, blank=True)
    provider_capture_id = models.CharField(max_length=100, blank=True)
    provider_transaction_id = models.CharField(max_length=100, blank=True)
    provider_refund_id = models.CharField(max_length=100, blank=True)
    refund_reference = models.CharField(max_length=50, blank=True, db_index=True)
    refund_amount_eur = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    payment_url = models.URLField(max_length=500, blank=True)
    qr_code_url = models.URLField(max_length=500, blank=True)
    amount_eur = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="EUR")
    status = models.CharField(
        max_length=30,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
        db_index=True,
    )
    payment_method = models.CharField(max_length=20, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    confirmation_sent_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(
                fields=("booking", "status"),
                name="payment_booking_status_idx",
            )
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("booking",),
                condition=models.Q(status=PaymentStatus.PENDING),
                name="one_pending_payment_per_booking",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_eur__gt=0),
                name="payment_amount_is_positive",
            ),
        ]

    @property
    def can_be_paid(self):
        return (
            self.status == PaymentStatus.PENDING
            and bool(self.payment_url)
            and (self.expires_at is None or self.expires_at > timezone.now())
        )

    def __str__(self):
        return f"{self.provider} {self.merchant_reference}"


class PaymentEvent(ImmutableFinancialRecord):
    """Deduplicated audit record for provider webhook processing."""

    payment_attempt = models.ForeignKey(
        PaymentAttempt,
        on_delete=models.PROTECT,
        related_name="events",
    )
    fingerprint = models.CharField(max_length=64, unique=True)
    event_type = models.CharField(max_length=50)
    provider_event_id = models.CharField(max_length=100, blank=True)
    provider_status = models.CharField(max_length=50, blank=True)
    details = models.JSONField(default=dict, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-received_at", "-id")


class BookingRefundStatus(models.TextChoices):
    PENDING = "pending", _("Refund pending")
    REFUNDED = "refunded", _("Refunded")
    FAILED = "failed", _("Refund failed")


class BookingRefund(ImmutableFinancialRecord):
    """One append-only total or partial refund against a valid booking sale.

    Refunds for duplicate or obsolete captures remain attached to the payment
    attempt itself because those captures never created a separate booking
    sale. Keeping the two concepts separate prevents an accidental credit note
    against the customer's valid receipt.
    """

    payment_attempt = models.ForeignKey(
        PaymentAttempt,
        on_delete=models.PROTECT,
        related_name="booking_refunds",
    )
    amount_eur = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(
        max_length=20,
        choices=BookingRefundStatus.choices,
        default=BookingRefundStatus.PENDING,
        db_index=True,
    )
    reason = models.CharField(max_length=500)
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
        related_name="requested_booking_refunds",
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    refunded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-requested_at", "-id")
        constraints = (
            models.CheckConstraint(
                condition=models.Q(amount_eur__gt=0),
                name="booking_refund_amount_is_positive",
            ),
        )


class BookingStay(models.Model):
    booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE, related_name="stays", related_query_name="stay"
    )

    pet = models.ForeignKey(
        Pet,
        on_delete=models.PROTECT,
        related_name="booking_stays",
        related_query_name="booking_stay",
    )

    catalog_item = models.ForeignKey(
        CatalogItem,
        on_delete=models.PROTECT,
        related_name="booking_stays",
    )
    catalog_offer = models.ForeignKey(
        "catalog.CatalogOffer",
        on_delete=models.PROTECT,
        related_name="booking_stays",
    )
    catalog_type_reference = models.PositiveBigIntegerField(editable=False)
    catalog_type_name_snapshot = models.CharField(max_length=120, editable=False)
    catalog_level_reference = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        editable=False,
    )
    catalog_level_name_snapshot = models.CharField(
        max_length=120,
        blank=True,
        editable=False,
    )
    toconline_item_id = models.CharField(max_length=100, blank=True)
    toconline_item_type = models.CharField(
        max_length=20,
        choices=CatalogItemType.choices,
    )
    item_name_snapshot = models.CharField(max_length=255)
    item_code_snapshot = models.CharField(max_length=100, blank=True)
    tax_code_snapshot = models.CharField(max_length=20, blank=True)
    unit_price_eur_snapshot = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
    currency_snapshot = models.CharField(max_length=3, default="EUR")
    discount_amount_eur_snapshot = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        editable=False,
    )

    start_date = models.DateField()
    end_date = models.DateField()

    notes = models.TextField(blank=True)

    @property
    def stay_name(self):
        return self.item_name_snapshot

    @property
    def unit_price_eur(self):
        return self.unit_price_eur_snapshot

    @property
    def duration_days(self):
        return (self.end_date - self.start_date).days

    @property
    def gross_accommodation_price_eur(self):
        return self.unit_price_eur * self.duration_days

    @property
    def accommodation_total_price_eur(self):
        return max(
            self.gross_accommodation_price_eur - self.discount_amount_eur_snapshot,
            Decimal("0.00"),
        )

    @property
    def gross_total_price_eur(self):
        return self.gross_accommodation_price_eur + sum(
            (service.gross_total_price_eur for service in self.services.all()),
            Decimal("0.00"),
        )

    @property
    def total_price_eur(self):
        return self.accommodation_total_price_eur + sum(
            (service.total_price_eur for service in self.services.all()),
            Decimal("0.00"),
        )

    @property
    def days_remaining(self):
        today = timezone.localdate()

        return max((self.end_date - today).days, 0)

    def clean(self):
        if self.pet.owner != self.booking.customer:
            raise ValidationError(_("Pet does not belong to the booking customer"))

        if self.end_date <= self.start_date:
            raise ValidationError(_("Check-out must be after check-in"))

        if self.catalog_offer_id and self.catalog_offer.catalog_item_id != self.catalog_item_id:
            raise ValidationError(
                {"catalog_offer": _("The catalog offer does not match the fiscal item.")}
            )

    class Meta:
        indexes = [
            models.Index(
                fields=("catalog_offer", "start_date", "end_date"),
                name="stay_offer_date_range_idx",
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gt=models.F("start_date")),
                name="booking_stay_has_positive_duration",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price_eur_snapshot__gte=0),
                name="booking_stay_price_is_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(discount_amount_eur_snapshot__gte=0),
                name="booking_stay_discount_not_negative",
            ),
        ]


class ServiceSchedulePreference(models.TextChoices):
    UNSCHEDULED = "unscheduled", _("No date preference")
    DATE = "date", _("Preferred date")
    DATE_TIME = "date_time", _("Preferred date and time")


class BookingService(models.Model):
    stay = models.ForeignKey(
        BookingStay, on_delete=models.CASCADE, related_name="services", related_query_name="service"
    )

    pet = models.ForeignKey(Pet, on_delete=models.PROTECT)

    catalog_item = models.ForeignKey(
        CatalogItem,
        on_delete=models.PROTECT,
        related_name="booking_services",
    )
    catalog_offer = models.ForeignKey(
        "catalog.CatalogOffer",
        on_delete=models.PROTECT,
        related_name="booking_services",
    )
    catalog_type_reference = models.PositiveBigIntegerField(editable=False)
    catalog_type_name_snapshot = models.CharField(max_length=120, editable=False)
    catalog_level_reference = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        editable=False,
    )
    catalog_level_name_snapshot = models.CharField(
        max_length=120,
        blank=True,
        editable=False,
    )
    toconline_item_id = models.CharField(max_length=100, blank=True)
    toconline_item_type = models.CharField(
        max_length=20,
        choices=CatalogItemType.choices,
    )
    item_name_snapshot = models.CharField(max_length=255)
    item_code_snapshot = models.CharField(max_length=100, blank=True)
    tax_code_snapshot = models.CharField(max_length=20, blank=True)
    unit_price_eur_snapshot = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
    currency_snapshot = models.CharField(max_length=3, default="EUR")
    discount_amount_eur_snapshot = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        editable=False,
    )

    schedule_preference = models.CharField(
        max_length=20,
        choices=ServiceSchedulePreference.choices,
        default=ServiceSchedulePreference.UNSCHEDULED,
    )
    preferred_date = models.DateField(null=True, blank=True)
    preferred_time = models.TimeField(null=True, blank=True)

    # The confirmed operational appointment, set or changed by management.
    scheduled_time = models.DateTimeField(null=True, blank=True)

    # For fixed/one-time services
    quantity = models.PositiveIntegerField(default=1)

    notes = models.TextField(blank=True)

    @property
    def service_name(self):
        return self.item_name_snapshot

    @property
    def unit_price_eur(self):
        return self.unit_price_eur_snapshot

    @property
    def gross_total_price_eur(self):
        return self.unit_price_eur * self.quantity

    @property
    def total_price_eur(self):
        return max(
            self.gross_total_price_eur - self.discount_amount_eur_snapshot,
            Decimal("0.00"),
        )

    def clean(self):
        errors = {}
        if self.quantity is not None and self.quantity < 1:
            errors["quantity"] = _("Quantity must be at least 1 for one-time services")

        if self.catalog_offer_id and self.catalog_offer.catalog_item_id != self.catalog_item_id:
            errors["catalog_offer"] = _("The catalog offer does not match the fiscal item.")

        if self.schedule_preference == ServiceSchedulePreference.UNSCHEDULED:
            if self.preferred_date:
                errors["preferred_date"] = _("Remove the date or choose a date preference.")
            if self.preferred_time:
                errors["preferred_time"] = _("Remove the time or choose a date and time.")
        elif self.schedule_preference == ServiceSchedulePreference.DATE:
            if not self.preferred_date:
                errors["preferred_date"] = _("Choose the preferred date.")
            if self.preferred_time:
                errors["preferred_time"] = _("Choose date and time to include a time.")
        elif self.schedule_preference == ServiceSchedulePreference.DATE_TIME:
            if not self.preferred_date:
                errors["preferred_date"] = _("Choose the preferred date.")
            if not self.preferred_time:
                errors["preferred_time"] = _("Choose the preferred time.")

        if self.scheduled_time and hasattr(self, "stay") and self.stay:
            # Validate scheduled_time is during the stay period
            if (
                self.scheduled_time.date() < self.stay.start_date
                or self.scheduled_time.date() >= self.stay.end_date
            ):
                errors["scheduled_time"] = _("Scheduled time must be within the stay period")

        if self.preferred_date and hasattr(self, "stay") and self.stay:
            if not self.stay.start_date <= self.preferred_date < self.stay.end_date:
                errors["preferred_date"] = _(
                    "The preferred date must be before check-out and within the stay."
                )

        if hasattr(self, "stay") and self.stay and self.pet_id != self.stay.pet_id:
            errors["pet"] = _("Service pet must match the stay pet")
        if errors:
            raise ValidationError(errors)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="booking_service_quantity_is_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price_eur_snapshot__gte=0),
                name="booking_service_price_is_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(discount_amount_eur_snapshot__gte=0),
                name="booking_service_discount_not_negative",
            ),
        ]


# ----------------------
# Media attached to stays
# ----------------------


def stay_media_upload_to(instance, filename):
    base, ext = os.path.splitext(filename)
    return f"stay_media/{instance.stay_id}/{uuid.uuid4().hex}{ext.lower()}"


class BookingStayMedia(models.Model):
    stay = models.ForeignKey(
        BookingStay,
        on_delete=models.CASCADE,
        related_name="media",
        related_query_name="medium",
    )
    file = models.FileField(upload_to=stay_media_upload_to)
    content_type = models.CharField(max_length=128, blank=True)
    original_filename = models.CharField(max_length=255, blank=True)
    size = models.BigIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_stay_media",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.stay_id} · {self.original_filename or self.file.name}"
