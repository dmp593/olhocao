from enum import StrEnum
from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from accounts.models import Account
from hotel.managers import BookingManager, BookingStayManager
from pets.models import Pet


class ServiceGroup(StrEnum):
    GENERAL_SERVICE = "G1"  # General Service
    PREMIUM_SERVICE = "G2"  # Premium Service
    EXTRA_SERVICE = "G3"    # Extra or Complementary Service
    SPECIAL_SERVICE = "G4"  # Special or Occasional Service
    OTHER_SERVICES = "G5"   # Other or Secondary Services


class BookingStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    CANCELLED = "cancelled", _("Cancelled")
    PAID = "paid", _("Paid")


class Booking(models.Model):
    account = models.ForeignKey(Account, on_delete=models.PROTECT)
    
    status = models.CharField(
        max_length=20,
        choices=BookingStatus.choices,
        default=BookingStatus.PENDING
    )

    stripe_session_id = models.CharField(
        max_length=100, blank=True
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
        return min(stay.start_date for stay in self.stays.all()) if self.stays.exists() else None
    
    @property
    def latest_end_date(self):
        return max(stay.end_date for stay in self.stays.all()) if self.stays.exists() else None

    @property
    def is_past(self):
        now = timezone.now().date()
        return any(stay.end_date < now for stay in self.stays.all())

    @property
    def is_active(self):
        now = timezone.now().date()
        return (self.status == BookingStatus.PAID and 
                any(stay.start_date <= now <= stay.end_date for stay in self.stays.all()))

    @property
    def is_upcoming(self):
        now = timezone.now().date()
        return (self.status == BookingStatus.PAID and 
                all(stay.start_date > now for stay in self.stays.all()))

    objects = BookingManager()

    def __str__(self):
        return f"Booking #{self.pk}"


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
    stay_id = models.CharField(max_length=50)
    stay_name = models.CharField(max_length=100)
    unit_price_eur = models.DecimalField(max_digits=10, decimal_places=2)

    start_date = models.DateField()
    end_date = models.DateField()

    notes = models.TextField(blank=True)

    @property
    def duration_days(self):
        return (self.end_date - self.start_date).days

    @property
    def total_price_eur(self):
        return self.unit_price_eur * self.duration_days + sum(svc.total_price_eur for svc in self.services.all())
    
    @property
    def days_remaining(self):
        today = timezone.now().date()

        if self.end_date >= today:
            return 0
        
        return (self.end_date - today).days

    objects = BookingStayManager()

    def clean(self):
        # Validate pet belongs to booking account user
        if self.pet.owner != self.booking.account.user:
            raise ValidationError("Pet does not belong to the booking account")


class ServiceType(models.TextChoices):
    DAILY = "daily", _("Daily Service")  # Repeats each day of stay
    TIMED = "timed", _("Timed Service")  # Specific date/time
    FIXED = "fixed", _("Fixed Service")  # Fixed/One-time regardless of duration


class BookingService(models.Model):
    stay = models.ForeignKey(
        BookingStay,
        on_delete=models.CASCADE,
        related_name="services",
        related_query_name="service"
    )

    pet = models.ForeignKey(Pet, on_delete=models.PROTECT)

    # Service Details (from billing system)
    service_id = models.CharField(max_length=50)
    service_name = models.CharField(max_length=100)
    service_type = models.CharField(
        max_length=20,
        choices=ServiceType.choices,
        default=ServiceType.FIXED
    )

    # Service Price
    unit_price_eur = models.DecimalField(max_digits=10, decimal_places=2)

    # For timed services
    scheduled_time = models.DateTimeField(null=True, blank=True)

    # For fixed/one-time services
    quantity = models.PositiveIntegerField(default=1)

    notes = models.TextField(blank=True)

    @property
    def total_price_eur(self):
        if self.service_type == ServiceType.DAILY:
            # Daily services multiply by stay duration
            return self.unit_price_eur * self.quantity * self.stay.duration_days
        elif self.service_type == ServiceType.TIMED:
            # Timed services are priced by unit price only (no quantity)
            return self.unit_price_eur
        else:  # FIXED
            # Fixed services can have quantity (e.g., 2 special treats)
            return self.unit_price_eur * self.quantity

    def clean(self):
        # Validate service type specific rules
        if self.service_type == ServiceType.TIMED:
            if not self.scheduled_time:
                raise ValidationError("Timed services require a scheduled time")
            if self.quantity != 1:
                raise ValidationError("Timed services must have quantity = 1")
                
        elif self.service_type == ServiceType.DAILY:
            if self.scheduled_time:
                raise ValidationError("Daily services cannot have a scheduled time")
            if self.quantity != 1:
                raise ValidationError("Daily services must have quantity = 1")
            if not hasattr(self, 'stay') or not self.stay:
                raise ValidationError("Daily services require a stay duration")
                
        elif self.service_type == ServiceType.ONE_TIME:
            if self.scheduled_time:
                raise ValidationError("One-time services cannot have a scheduled time")
            if self.quantity < 1:
                raise ValidationError("Quantity must be ≥ 1 for one-time services")
                
        if self.scheduled_time and hasattr(self, 'stay') and self.stay:
            # Validate scheduled_time is during the stay period
            if self.scheduled_time.date() < self.stay.start_date or self.scheduled_time.date() > self.stay.end_date:
                raise ValidationError(
                    "Scheduled time must be within the stay period"
                )


class PaymentStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    COMPLETED = "completed", _("Completed")
    FAILED = "failed", _("Failed")
