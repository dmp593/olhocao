import os
import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.templatetags.static import static
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

FRONTOFFICE_IMAGE_EXTENSIONS = ("gif", "jpeg", "jpg", "png", "webp")


def frontoffice_image_upload_to(instance, filename):
    _, extension = os.path.splitext(filename)
    return f"frontoffice/{instance._meta.model_name}/{uuid.uuid4().hex}{extension.lower()}"


def validate_frontoffice_image_size(image):
    if image.size > settings.FRONTOFFICE_IMAGE_MAX_UPLOAD_BYTES:
        raise ValidationError(_("The website image is too large."))


class ContentDestination(models.TextChoices):
    NONE = "", _("No link")
    PRICING = "pricing", _("Prices")
    HOTEL = "hotel", _("Hotel booking")
    ACTIVITIES = "activities", _("Daycare and training")
    CONTACT = "contact", _("Contact")
    ABOUT = "about", _("About us")


DESTINATION_ROUTE_NAMES = {
    ContentDestination.PRICING: "frontoffice:pricing",
    ContentDestination.HOTEL: "hotel:booking_stay",
    ContentDestination.ACTIVITIES: "activities:offering_list",
    ContentDestination.CONTACT: "frontoffice:contact_us",
    ContentDestination.ABOUT: "frontoffice:about_us",
}


class ManagedContentEntry(models.Model):
    """Shared lifecycle fields for editable, non-transactional website content."""

    order = models.PositiveIntegerField(default=0, db_index=True, verbose_name=_("Order"))
    is_active = models.BooleanField(default=True, db_index=True, verbose_name=_("Published"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("Last edited by"),
    )

    class Meta:
        abstract = True
        ordering = ("order", "id")


class ManagedImageMixin(models.Model):
    image = models.ImageField(
        upload_to=frontoffice_image_upload_to,
        blank=True,
        validators=(
            FileExtensionValidator(allowed_extensions=FRONTOFFICE_IMAGE_EXTENSIONS),
            validate_frontoffice_image_size,
        ),
        verbose_name=_("Image"),
    )
    static_image_path = models.CharField(max_length=255, blank=True, editable=False)
    image_alt = models.CharField(max_length=180, verbose_name=_("Image description"))

    class Meta:
        abstract = True

    @property
    def image_url(self):
        if self.image:
            return self.image.url
        if self.static_image_path:
            return static(self.static_image_path)
        return ""


class SiteSettings(models.Model):
    """The single editable set of public contact and section copy."""

    company_name = models.CharField(max_length=120, default="Olhó Cão")
    public_email = models.EmailField(default="olhocao@olhocao.pt")
    primary_phone = models.CharField(max_length=32, default="916 749 407")
    secondary_phone = models.CharField(max_length=32, default="924 454 382", blank=True)
    address_line_1 = models.CharField(max_length=180, default="Rua Quinta dos Frades, 904")
    address_line_2 = models.CharField(max_length=180, default="2400-821 Leiria, Portugal")
    maps_embed_url = models.URLField(max_length=1200)
    directions_url = models.URLField(max_length=500)
    weekday_hours = models.CharField(max_length=180)
    weekend_hours = models.CharField(max_length=180)

    home_services_eyebrow = models.CharField(max_length=120)
    home_services_title = models.CharField(max_length=180)
    home_services_subtitle = models.TextField()
    home_testimonials_eyebrow = models.CharField(max_length=120)
    home_testimonials_title = models.CharField(max_length=180)
    home_testimonials_subtitle = models.TextField()

    about_eyebrow = models.CharField(max_length=120)
    about_title = models.CharField(max_length=180)
    about_subtitle = models.CharField(max_length=240)
    about_history_title = models.CharField(max_length=180)
    about_history = models.TextField()
    facility_title = models.CharField(max_length=180)
    facility_description = models.CharField(max_length=240)
    team_title = models.CharField(max_length=180)
    team_subtitle = models.CharField(max_length=240)

    contact_eyebrow = models.CharField(max_length=120)
    contact_title = models.CharField(max_length=180)
    contact_subtitle = models.CharField(max_length=240)
    contact_visit_notice = models.TextField()

    pricing_eyebrow = models.CharField(max_length=120)
    pricing_title = models.CharField(max_length=180)
    pricing_subtitle = models.TextField()

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("Last edited by"),
    )

    class Meta:
        verbose_name = _("Website settings")
        verbose_name_plural = _("Website settings")
        constraints = (
            models.CheckConstraint(
                condition=models.Q(id=1),
                name="frontoffice_has_one_site_settings_row",
            ),
        )

    def save(self, *args, **kwargs):
        self.pk = 1
        return super().save(*args, **kwargs)

    @classmethod
    def current(cls):
        return cls.objects.filter(pk=1).first()

    def __str__(self):
        return str(_("Website settings"))

    @staticmethod
    def _phone_uri(phone_number):
        return re.sub(r"[^+\d]", "", phone_number)

    @property
    def primary_phone_uri(self):
        return self._phone_uri(self.primary_phone)

    @property
    def secondary_phone_uri(self):
        return self._phone_uri(self.secondary_phone)


class HomeSlideKind(models.TextChoices):
    HERO = "hero", _("Main carousel")
    BENEFIT = "benefit", _("Benefits carousel")


class HomeSlide(ManagedContentEntry, ManagedImageMixin):
    kind = models.CharField(max_length=20, choices=HomeSlideKind.choices)
    title = models.CharField(max_length=180)
    description = models.TextField()
    cta_text = models.CharField(max_length=120, blank=True)
    cta_destination = models.CharField(
        max_length=20,
        blank=True,
        choices=ContentDestination.choices,
    )

    @property
    def cta_link(self):
        route_name = DESTINATION_ROUTE_NAMES.get(self.cta_destination)
        return reverse(route_name) if route_name else ""

    def __str__(self):
        return self.title


class ServiceHighlight(ManagedContentEntry):
    class Icon(models.TextChoices):
        HOTEL = "hotel", _("Hotel")
        DAYCARE = "daycare", _("Daycare")
        TRAINING = "training", _("Training")
        GROOMING = "grooming", _("Grooming")

    ICON_TEMPLATES = {
        Icon.HOTEL: "icons/pet_hotel.html",
        Icon.DAYCARE: "icons/dog_running.html",
        Icon.TRAINING: "icons/dog_training.html",
        Icon.GROOMING: "icons/grooming.html",
    }

    icon = models.CharField(max_length=20, choices=Icon.choices)
    title = models.CharField(max_length=180)
    description = models.TextField()
    features = models.TextField(help_text=_("Enter one feature per line."))
    action_text = models.CharField(max_length=120)
    catalog_type = models.ForeignKey(
        "catalog.CatalogType",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="homepage_highlights",
        help_text=_(
            "Optional catalog destination. The link follows this type even when its name changes."
        ),
    )
    destination = models.CharField(
        max_length=20,
        blank=True,
        choices=ContentDestination.choices,
        help_text=_("Use only for a page that is not represented by a catalog type."),
    )

    @property
    def icon_template(self):
        return self.ICON_TEMPLATES[self.icon]

    @property
    def feature_list(self):
        return [feature.strip() for feature in self.features.splitlines() if feature.strip()]

    @property
    def destination_url(self):
        if self.catalog_type_id and self.catalog_type.is_active:
            return self.catalog_type.public_url
        route_name = DESTINATION_ROUTE_NAMES.get(self.destination)
        return reverse(route_name) if route_name else ""

    def clean(self):
        super().clean()
        if self.catalog_type_id and self.destination:
            raise ValidationError(
                {
                    "destination": _(
                        "Choose either a catalog type or another website page, not both."
                    )
                }
            )

    def __str__(self):
        return self.title


class Testimonial(ManagedContentEntry, ManagedImageMixin):
    headline = models.CharField(max_length=180)
    body = models.TextField()
    attribution = models.CharField(
        max_length=180,
        blank=True,
        help_text=_("Optional customer or dog name. Leave empty for a care principle."),
    )

    def __str__(self):
        return self.headline


class TeamMember(ManagedContentEntry, ManagedImageMixin):
    name = models.CharField(max_length=120)
    role = models.CharField(max_length=180)
    description = models.TextField()
    specialization = models.CharField(max_length=240)

    def __str__(self):
        return self.name


class Partner(ManagedContentEntry):
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=180)
    website_url = models.URLField(max_length=500)
    symbol = models.CharField(
        max_length=16,
        blank=True,
        help_text=_("Optional short symbol or emoji shown before the partner."),
    )
    tagline = models.CharField(max_length=180, blank=True)

    def __str__(self):
        return self.name


class SocialLink(ManagedContentEntry):
    class Platform(models.TextChoices):
        FACEBOOK = "facebook", "Facebook"
        INSTAGRAM = "instagram", "Instagram"
        LINKEDIN = "linkedin", "LinkedIn"
        YOUTUBE = "youtube", "YouTube"
        TIKTOK = "tiktok", "TikTok"
        WEBSITE = "website", _("Website")

    platform = models.CharField(max_length=20, choices=Platform.choices)
    url = models.URLField(max_length=500)

    def __str__(self):
        return self.get_platform_display()


class ContactSubject(models.Model):
    name = models.CharField(
        max_length=100, verbose_name=_("Name"), help_text=_("Contact Subject Name")
    )

    order = models.IntegerField(
        default=999, verbose_name=_("Order"), help_text=_("Contact Subject Order")
    )

    is_active = models.BooleanField(default=True, db_index=True, verbose_name=_("Published"))
    updated_at = models.DateTimeField(auto_now=True, null=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("Last edited by"),
    )

    def __str__(self):
        return self.name

    class Meta:
        ordering = ("order", "id")


class ContactRequest(models.Model):
    subject = models.ForeignKey(
        to=ContactSubject,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Subject"),
        help_text=_("Contact Subject"),
    )

    name = models.CharField(
        max_length=128,
        verbose_name=_("Name"),
        help_text=_("Your Name"),
    )

    phone = models.CharField(max_length=32, verbose_name=_("Phone"), help_text=_("Your Phone"))

    email = models.EmailField(verbose_name=_("Email"), help_text=_("Your Email Account"))

    message = models.TextField(verbose_name=_("Message"), help_text=_("Your Message"))

    created_at = models.DateTimeField(
        null=True,
        blank=True,
        auto_now_add=True,
        verbose_name=_("Created At"),
        help_text=_("When the contact request was created"),
    )

    read_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Read At"),
        help_text=_("When the contact request was read"),
    )

    class Meta:
        ordering = ["-created_at"]
