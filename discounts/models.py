from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from olhocao.models import ImmutableFinancialRecord


class DiscountType(models.TextChoices):
    FIXED = "fixed", _("Fixed amount")
    PERCENTAGE = "percentage", _("Percentage")


class SaleChannel(models.TextChoices):
    """Stable ways in which a catalog offer can be sold.

    These values describe application behavior, never customer-facing catalog
    names. Renaming Hotel, ATL, a level or an offer therefore cannot change
    promotion eligibility.
    """

    DATE_RANGE = "date_range", _("Date-range reservation")
    ADD_ON = "add_on", _("Service added to another purchase")
    ONE_OFF = "one_off", _("One-off purchase")
    PACK = "pack", _("Pack")
    SUBSCRIPTION = "subscription", _("Subscription")


class DiscountSource(models.TextChoices):
    NONE = "none", _("No discount")
    PROMOTION = "promotion", _("Promotion code")
    STAFF = "staff", _("Staff adjustment")


class RedemptionStatus(models.TextChoices):
    RESERVED = "reserved", _("Reserved")
    CONSUMED = "consumed", _("Consumed")
    RELEASED = "released", _("Released")


class PurchaseKind(models.TextChoices):
    HOTEL_BOOKING = "hotel_booking", _("Hotel booking")
    ACTIVITY_ENTITLEMENT = "activity_entitlement", _("Activity service")


class PromotionChangeType(models.TextChoices):
    CREATED = "created", _("Created")
    UPDATED = "updated", _("Updated")
    ACTIVATED = "activated", _("Activated")
    DEACTIVATED = "deactivated", _("Deactivated")


def normalize_promotion_code(value):
    return str(value or "").strip().upper()


def normalize_breed_name(value):
    return " ".join(str(value or "").split()).casefold()


class Promotion(models.Model):
    """A reusable commercial discount whose eligibility is based on capabilities.

    Commercial names never influence eligibility. A promotion targets stable
    sale channels and can then be narrowed to dynamic catalog types, levels,
    offers, host services, customers, dogs or breed names.
    """

    code = models.CharField(
        max_length=40,
        unique=True,
        validators=[
            RegexValidator(
                regex=r"^[A-Za-z0-9_-]+$",
                message=_("Use only letters, numbers, hyphens and underscores."),
            )
        ],
    )
    name = models.CharField(max_length=150)
    discount_type = models.CharField(max_length=20, choices=DiscountType.choices)
    value = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"))],
    )
    eligible_sale_channels = models.JSONField(
        default=list,
        help_text=_("The stable purchase flows where this promotion can be used."),
    )
    catalog_types = models.ManyToManyField(
        "catalog.CatalogType",
        blank=True,
        related_name="promotions",
        help_text=_("Leave empty to include offers from every catalog type."),
    )
    catalog_levels = models.ManyToManyField(
        "catalog.CatalogLevel",
        blank=True,
        related_name="promotions",
        help_text=_("Leave empty to include every level in the selected catalog types."),
    )
    catalog_offers = models.ManyToManyField(
        "catalog.CatalogOffer",
        blank=True,
        related_name="promotions",
        help_text=_("Leave empty to include every matching offer."),
    )
    host_catalog_types = models.ManyToManyField(
        "catalog.CatalogType",
        blank=True,
        related_name="hosted_add_on_promotions",
        help_text=_("Optional. Restrict an add-on to the service in which it is purchased."),
    )
    customers = models.ManyToManyField(
        "accounts.CustomerProfile",
        blank=True,
        related_name="promotions",
        help_text=_("Leave empty to allow every customer."),
    )
    pets = models.ManyToManyField(
        "pets.Pet",
        blank=True,
        related_name="promotions",
        help_text=_("Leave empty unless the promotion is for specific dogs."),
    )
    eligible_breeds = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Normalized breed names. Leave empty to allow every breed."),
    )
    starts_at = models.DateTimeField(null=True, blank=True, db_index=True)
    ends_at = models.DateTimeField(null=True, blank=True, db_index=True)
    service_starts_on = models.DateField(
        null=True,
        blank=True,
        help_text=_("Optional first eligible service date."),
    )
    service_ends_on = models.DateField(
        null=True,
        blank=True,
        help_text=_("Optional last eligible service date."),
    )
    minimum_eligible_amount_eur = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    max_redemptions = models.PositiveIntegerField(null=True, blank=True)
    max_redemptions_per_customer = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_promotions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-is_active", "code")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(value__gt=0),
                name="promotion_value_is_positive",
            ),
            models.CheckConstraint(
                condition=(models.Q(discount_type=DiscountType.FIXED) | models.Q(value__lte=100)),
                name="promotion_percentage_not_over_100",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(max_redemptions__isnull=True) | models.Q(max_redemptions__gte=1)
                ),
                name="promotion_max_redemptions_positive",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(max_redemptions_per_customer__isnull=True)
                    | models.Q(max_redemptions_per_customer__gte=1)
                ),
                name="promotion_customer_limit_positive",
            ),
        ]
        indexes = [
            models.Index(
                fields=("is_active", "starts_at", "ends_at"), name="promo_active_window_idx"
            )
        ]

    def clean(self):
        super().clean()
        # Codes are stored in one canonical form so uniqueness behaves the same
        # on SQLite, PostgreSQL and MariaDB.
        self.code = normalize_promotion_code(self.code)
        errors = {}
        channels = list(dict.fromkeys(self.eligible_sale_channels or []))
        invalid_channels = sorted(set(channels) - set(SaleChannel.values))
        if invalid_channels:
            errors["eligible_sale_channels"] = _("One or more sale channels are invalid.")
        elif not channels:
            errors["eligible_sale_channels"] = _("Choose at least one sale channel.")
        else:
            self.eligible_sale_channels = channels

        if (
            self.discount_type == DiscountType.PERCENTAGE
            and self.value is not None
            and self.value > Decimal("100")
        ):
            errors["value"] = _("A percentage discount cannot exceed 100%.")
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            errors["ends_at"] = _("The end must be after the start.")
        if (
            self.service_starts_on
            and self.service_ends_on
            and self.service_ends_on < self.service_starts_on
        ):
            errors["service_ends_on"] = _("The last service date cannot precede the first.")

        normalized_breeds = []
        seen_breeds = set()
        for breed in self.eligible_breeds or []:
            normalized = normalize_breed_name(breed)
            if normalized and normalized not in seen_breeds:
                normalized_breeds.append(normalized)
                seen_breeds.add(normalized)
        self.eligible_breeds = normalized_breeds
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.code = normalize_promotion_code(self.code)
        return super().save(*args, **kwargs)

    @property
    def is_current(self):
        now = timezone.now()
        return (
            self.is_active
            and (not self.starts_at or self.starts_at <= now)
            and (not self.ends_at or now < self.ends_at)
        )

    @property
    def sale_channel_labels(self):
        labels = dict(SaleChannel.choices)
        return [labels[value] for value in self.eligible_sale_channels if value in labels]

    def __str__(self):
        return f"{self.code} · {self.name}"


class PromotionRedemption(ImmutableFinancialRecord):
    """Explicit, append-only record used for limits and purchase audit."""

    promotion = models.ForeignKey(
        Promotion,
        on_delete=models.PROTECT,
        related_name="redemptions",
    )
    customer = models.ForeignKey(
        "accounts.CustomerProfile",
        on_delete=models.PROTECT,
        related_name="promotion_redemptions",
    )
    purchase_kind = models.CharField(max_length=30, choices=PurchaseKind.choices)
    purchase_reference = models.CharField(max_length=100, db_index=True)
    active_purchase_reference = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        unique=True,
        editable=False,
        help_text=_("Set while this reservation counts towards promotion limits."),
    )
    status = models.CharField(
        max_length=20,
        choices=RedemptionStatus.choices,
        default=RedemptionStatus.RESERVED,
        db_index=True,
    )
    subtotal_amount_eur = models.DecimalField(max_digits=12, decimal_places=2)
    eligible_amount_eur = models.DecimalField(max_digits=12, decimal_places=2)
    discount_amount_eur = models.DecimalField(max_digits=12, decimal_places=2)
    code_snapshot = models.CharField(max_length=40)
    name_snapshot = models.CharField(max_length=150)
    discount_type_snapshot = models.CharField(max_length=20, choices=DiscountType.choices)
    value_snapshot = models.DecimalField(max_digits=12, decimal_places=4)
    reserved_at = models.DateTimeField(auto_now_add=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-reserved_at", "-id")
        indexes = [
            models.Index(fields=("promotion", "status"), name="promo_redemption_status_idx"),
            models.Index(fields=("customer", "status"), name="promo_customer_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(subtotal_amount_eur__gte=0),
                name="redemption_subtotal_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(eligible_amount_eur__gt=0),
                name="redemption_eligible_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(discount_amount_eur__gt=0),
                name="redemption_discount_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(eligible_amount_eur__lte=models.F("subtotal_amount_eur")),
                name="redemption_eligible_within_subtotal",
            ),
            models.CheckConstraint(
                condition=models.Q(discount_amount_eur__lte=models.F("eligible_amount_eur")),
                name="redemption_discount_within_eligible",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=(RedemptionStatus.RESERVED, RedemptionStatus.CONSUMED),
                        active_purchase_reference__isnull=False,
                    )
                    | models.Q(
                        status=RedemptionStatus.RELEASED,
                        active_purchase_reference__isnull=True,
                    )
                ),
                name="redemption_active_reference_matches_status",
            ),
        ]

    def __str__(self):
        return f"{self.code_snapshot} · {self.purchase_reference}"


class PromotionChangeEvent(ImmutableFinancialRecord):
    promotion = models.ForeignKey(
        Promotion,
        on_delete=models.PROTECT,
        related_name="change_events",
    )
    change_type = models.CharField(max_length=20, choices=PromotionChangeType.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="promotion_change_events",
    )
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")


class DiscountSnapshotMixin(models.Model):
    """Fields frozen on a purchase so later promotion edits cannot rewrite it."""

    discount_source = models.CharField(
        max_length=20,
        choices=DiscountSource.choices,
        default=DiscountSource.NONE,
        editable=False,
    )
    promotion = models.ForeignKey(
        "discounts.Promotion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s_purchases",
        editable=False,
    )
    promotion_code_snapshot = models.CharField(max_length=40, blank=True, editable=False)
    promotion_name_snapshot = models.CharField(max_length=150, blank=True, editable=False)
    promotion_discount_type_snapshot = models.CharField(
        max_length=20,
        choices=DiscountType.choices,
        blank=True,
        editable=False,
    )
    promotion_value_snapshot = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        editable=False,
    )
    discount_amount_eur_snapshot = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        editable=False,
    )
    discount_reason_snapshot = models.CharField(max_length=500, blank=True, editable=False)
    discount_applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(app_label)s_%(class)s_price_adjustments",
        editable=False,
    )
    discount_applied_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        abstract = True

    @property
    def has_discount(self):
        return self.discount_amount_eur_snapshot > Decimal("0.00")
