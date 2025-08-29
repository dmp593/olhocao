from django.db import models
from django.conf import settings
import os
import uuid
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from djstripe.models import (
    Product as StripeProduct,
    Price as StripePrice
)

from accounts.models import Account
from pets.models import Pet


class BookingStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    CANCELLED = "cancelled", _("Cancelled")
    PAID = "paid", _("Paid")


class Booking(models.Model):
    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT
    )

    status = models.CharField(
        max_length=20,
        choices=BookingStatus.choices,
        default=BookingStatus.PENDING
    )

    stripe_checkout_session_id = models.CharField(
        max_length=100,
        blank=True
    )

    toconline_sale_document_id = models.CharField(
        max_length=100,
        blank=True
    )

    toconline_amend_document_id = models.CharField(
        max_length=100,
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    notes = models.TextField(blank=True)

    @property
    def total_price_eur(self):
        return sum(stay.total_price_eur for stay in self.stays.all())
    
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
        now = timezone.now().date()
        return any(stay.end_date < now for stay in self.stays.all())

    @property
    def is_active(self):
        now = timezone.now().date()
        return (
            self.status == BookingStatus.PAID
            and any(
                stay.start_date <= now <= stay.end_date
                for stay in self.stays.all()
            )
        )

    @property
    def is_upcoming(self):
        now = timezone.now().date()
        return (
            self.status == BookingStatus.PAID
            and all(stay.start_date > now for stay in self.stays.all())
        )

    @property
    def can_modify(self):
        days_until_start = self.earliest_start_date - timezone.now().date()
        return (
            days_until_start.days >= 7
            and self.status != BookingStatus.CANCELLED
        )

    @property
    def can_cancel(self):
        days_until_start = self.earliest_start_date - timezone.now().date()
        return (
            days_until_start.days >= 7
            and self.status != BookingStatus.CANCELLED
        )

    def __str__(self):
        return _("Booking") + f" #{self.pk}"


class BookingStay(models.Model):
    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name="stays",
        related_query_name="stay"
    )

    pet = models.ForeignKey(
        Pet,
        on_delete=models.PROTECT,
        related_name='booking_stays',
        related_query_name='booking_stay',
    )

    # Stay information from external API
    stripe_product_id = models.CharField(max_length=50)

    start_date = models.DateField()
    end_date = models.DateField()

    notes = models.TextField(blank=True)

    @property
    def stripe_product(self):
        return StripeProduct.objects.get(id=self.stripe_product_id)
    
    @property
    def stripe_price(self):
        return StripePrice.objects.get(product=self.stripe_product_id)

    @property
    def stay_name(self):
        return self.stripe_product.name
    
    @property
    def unit_price_eur(self):
        return self.stripe_price.unit_amount

    @property
    def duration_days(self):
        return (self.end_date - self.start_date).days

    @property
    def total_price_eur(self):
        return (
            self.unit_price_eur * self.duration_days
            + sum(svc.total_price_eur for svc in self.services.all())
        )
    
    @property
    def days_remaining(self):
        today = timezone.now().date()

        if self.end_date >= today:
            return 0
        
        return (self.end_date - today).days

    def clean(self):
        # Validate pet belongs to booking account user
        if self.pet.owner != self.booking.account.user:
            raise ValidationError("Pet does not belong to the booking account")


class BookingService(models.Model):
    stay = models.ForeignKey(
        BookingStay,
        on_delete=models.CASCADE,
        related_name="services",
        related_query_name="service"
    )

    pet = models.ForeignKey(Pet, on_delete=models.PROTECT)

    # Service Details (from billing system)
    stripe_product_id = models.CharField(max_length=50)

    # For timed services
    scheduled_time = models.DateTimeField(null=True, blank=True)

    # For fixed/one-time services
    quantity = models.PositiveIntegerField(default=1)

    notes = models.TextField(blank=True)

    @property
    def stripe_product(self):
        return StripeProduct.objects.get(id=self.stripe_product_id)
    
    @property
    def stripe_price(self):
        return StripePrice.objects.get(product=self.stripe_product_id)

    @property
    def service_name(self):
        return self.stripe_product.name

    @property
    def unit_price_eur(self):
        return self.stripe_price.unit_amount

    @property
    def total_price_eur(self):
        return self.unit_price_eur * self.quantity

    def clean(self):
        if self.quantity < 1:
            raise ValidationError("Quantity must be ≥ 1 for one-time services")

        if self.scheduled_time and hasattr(self, 'stay') and self.stay:
            # Validate scheduled_time is during the stay period
            if (
                self.scheduled_time.date() < self.stay.start_date
                or self.scheduled_time.date() > self.stay.end_date
            ):
                raise ValidationError(
                    "Scheduled time must be within the stay period"
                )


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
