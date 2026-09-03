import os
import uuid

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.templatetags.static import static
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from hotel.models import CatalogBookingKind, CatalogItem

CATALOG_IMAGE_EXTENSIONS = ("gif", "jpeg", "jpg", "png", "webp")


class CatalogWorkflow(models.TextChoices):
    """Stable behavior for a dynamically named catalog type."""

    DATE_RANGE = "date_range", _("Date range with overnight pricing")
    FULL_DAY_SLOT = "full_day_slot", _("Full-day capacity calendar")
    TIMED_SLOT = "timed_slot", _("Timed capacity calendar")
    FLEXIBLE = "flexible", _("Flexible fulfilment")


def catalog_taxonomy_image_upload_to(instance, filename):
    _, extension = os.path.splitext(filename)
    return f"catalog/taxonomy/{instance._meta.model_name}/{uuid.uuid4().hex}{extension.lower()}"


def validate_catalog_taxonomy_image_size(image):
    if image.size > settings.CATALOG_IMAGE_MAX_UPLOAD_BYTES:
        raise ValidationError(_("The catalog image is too large."))


class CatalogImageMixin(models.Model):
    image = models.ImageField(
        upload_to=catalog_taxonomy_image_upload_to,
        blank=True,
        validators=(
            FileExtensionValidator(allowed_extensions=CATALOG_IMAGE_EXTENSIONS),
            validate_catalog_taxonomy_image_size,
        ),
        verbose_name=_("Image"),
    )
    image_alt = models.CharField(max_length=180, blank=True, verbose_name=_("Image description"))

    class Meta:
        abstract = True


class CatalogType(CatalogImageMixin, models.Model):
    """A customer-facing service family with stable operational behavior.

    Names such as ATL, Hotel or Training are content. ``workflow`` is the
    invariant that tells the application whether it books nights, days, timed
    sessions or performs a simple sale.
    """

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    description = models.TextField(blank=True)
    workflow = models.CharField(max_length=24, choices=CatalogWorkflow.choices, db_index=True)
    default_capacity = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text=_("Default maximum number of dogs for this service."),
    )
    default_cancellation_notice_hours = models.PositiveSmallIntegerField(
        default=24,
        help_text=_("Default notice required to return a session credit."),
    )
    closes_on_national_holidays = models.BooleanField(
        default=False,
        help_text=_("Block new reservations on Portuguese national public holidays."),
    )
    order = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_catalog_types",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("order", "name", "id")

    def clean(self):
        if (
            self.is_active
            and self.workflow != CatalogWorkflow.FLEXIBLE
            and not self.default_capacity
        ):
            raise ValidationError(
                {"default_capacity": _("Set the operational capacity before activating this type.")}
            )

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._available_slug()
        return super().save(*args, **kwargs)

    def _available_slug(self):
        base = slugify(self.name) or "service"
        candidate = base
        suffix = 2
        queryset = type(self).objects.exclude(pk=self.pk)
        while queryset.filter(slug=candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    @property
    def is_scheduled(self):
        return self.workflow in {
            CatalogWorkflow.FULL_DAY_SLOT,
            CatalogWorkflow.TIMED_SLOT,
        }

    @property
    def public_url(self):
        if self.workflow == CatalogWorkflow.DATE_RANGE:
            return reverse("hotel:booking_stay")
        return f"{reverse('activities:offering_list')}?type={self.slug}"

    @property
    def image_url(self):
        return self.image.url if self.image else ""

    def __str__(self):
        return self.name


class CatalogLevel(CatalogImageMixin, models.Model):
    """An optional tier or operational variant inside a catalog type."""

    catalog_type = models.ForeignKey(
        CatalogType,
        on_delete=models.PROTECT,
        related_name="levels",
    )
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140)
    description = models.TextField(blank=True)
    capacity_override = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text=_("Leave empty to use the capacity configured on the type."),
    )
    cancellation_notice_hours_override = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text=_("Leave empty to use the cancellation policy configured on the type."),
    )
    order = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_catalog_levels",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("catalog_type__order", "order", "name", "id")
        constraints = (
            models.UniqueConstraint(
                fields=("catalog_type", "slug"),
                name="unique_catalog_level_slug_per_type",
            ),
        )

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._available_slug()
        return super().save(*args, **kwargs)

    def _available_slug(self):
        base = slugify(self.name) or "level"
        candidate = base
        suffix = 2
        queryset = type(self).objects.filter(catalog_type=self.catalog_type).exclude(pk=self.pk)
        while queryset.filter(slug=candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    @property
    def effective_capacity(self):
        return self.capacity_override or self.catalog_type.default_capacity

    @property
    def effective_cancellation_notice_hours(self):
        if self.cancellation_notice_hours_override is not None:
            return self.cancellation_notice_hours_override
        return self.catalog_type.default_cancellation_notice_hours

    @property
    def image_url(self):
        if self.image:
            return self.image.url
        return self.catalog_type.image_url

    @property
    def image_source_label(self):
        return _("Level") if self.image else _("Catalog type")

    def __str__(self):
        return f"{self.catalog_type.name} · {self.name}"


class CatalogOffer(CatalogImageMixin, models.Model):
    """A sellable price or plan backed by one current TOConline projection."""

    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    catalog_type = models.ForeignKey(
        CatalogType,
        on_delete=models.PROTECT,
        related_name="offers",
    )
    level = models.ForeignKey(
        CatalogLevel,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="offers",
    )
    catalog_item = models.OneToOneField(
        CatalogItem,
        on_delete=models.PROTECT,
        related_name="catalog_offer",
    )
    is_standalone = models.BooleanField(
        default=True,
        help_text=_("Allow this price or plan to be purchased outside another service."),
    )
    is_public = models.BooleanField(default=False, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    order = models.PositiveIntegerField(default=0, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_catalog_offers",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("catalog_type__order", "level__order", "order", "catalog_item__name")

    def clean(self):
        errors = {}
        if self.level_id and self.level.catalog_type_id != self.catalog_type_id:
            errors["level"] = _("The selected level belongs to a different catalog type.")
        if self.catalog_item_id:
            expected_booking_kind = (
                CatalogBookingKind.STAY
                if self.catalog_type.workflow == CatalogWorkflow.DATE_RANGE
                else CatalogBookingKind.OTHER
            )
            if self.catalog_item.booking_kind != expected_booking_kind:
                errors["catalog_item"] = _("The fiscal item is not configured for this workflow.")
        if errors:
            raise ValidationError(errors)

    @property
    def effective_capacity(self):
        if self.level_id:
            return self.level.effective_capacity
        return self.catalog_type.default_capacity

    @property
    def effective_cancellation_notice_hours(self):
        if self.level_id:
            return self.level.effective_cancellation_notice_hours
        return self.catalog_type.default_cancellation_notice_hours

    @property
    def image_url(self):
        if self.image:
            return self.image.url
        if self.level_id and self.level.image_url:
            return self.level.image_url
        if self.catalog_type.image_url:
            return self.catalog_type.image_url
        return static(self.catalog_item.fallback_image_static_path)

    @property
    def image_source_label(self):
        if self.image:
            return _("Price or plan")
        if self.level_id and self.level.image:
            return _("Level")
        if self.catalog_type.image:
            return _("Catalog type")
        return _("Website default")

    @property
    def is_available(self):
        return self.is_active and self.catalog_type.is_active and self.catalog_item.is_available

    def __str__(self):
        return self.name


class AddOnAvailability(models.Model):
    """Makes one reusable price or plan available as an optional service."""

    add_on = models.ForeignKey(
        CatalogOffer,
        on_delete=models.PROTECT,
        related_name="add_on_availabilities",
    )
    target_type = models.ForeignKey(
        CatalogType,
        on_delete=models.PROTECT,
        related_name="available_add_ons",
    )
    target_level = models.ForeignKey(
        CatalogLevel,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="available_add_ons",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_add_on_availabilities",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("target_type__order", "target_level__order", "add_on__order")
        constraints = (
            models.UniqueConstraint(
                fields=("add_on", "target_type"),
                condition=models.Q(target_level__isnull=True),
                name="unique_add_on_per_catalog_type",
            ),
            models.UniqueConstraint(
                fields=("add_on", "target_level"),
                condition=models.Q(target_level__isnull=False),
                name="unique_add_on_per_catalog_level",
            ),
        )

    def clean(self):
        errors = {}
        if self.target_level_id and self.target_level.catalog_type_id != self.target_type_id:
            errors["target_level"] = _("The selected level belongs to a different catalog type.")
        if self.add_on_id:
            # Optional services are operational one-off deliveries (for example,
            # grooming).  Their commercial names remain completely dynamic.
            from activities.models import OfferingMode

            if self.add_on.catalog_type_id == self.target_type_id:
                errors["add_on"] = _(
                    "An optional service cannot be attached to its own catalog type."
                )
            try:
                offering = self.add_on.activity_offering
            except ObjectDoesNotExist:
                offering = None
            if (
                self.add_on.catalog_type.workflow != CatalogWorkflow.FLEXIBLE
                or offering is None
                or offering.mode != OfferingMode.ONE_OFF
            ):
                errors["add_on"] = _("Choose a flexible service sold as a one-off purchase.")
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        target = self.target_level or self.target_type
        return f"{self.add_on} → {target}"
