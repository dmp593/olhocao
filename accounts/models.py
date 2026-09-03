from django.contrib.auth import get_user_model
from django.db import models
from django.utils.translation import gettext_lazy as _

User = get_user_model()


class CustomerSyncStatus(models.TextChoices):
    PENDING = "pending", _("Waiting to synchronize")
    SYNCED = "synced", _("Synchronized")
    FAILED = "failed", _("Synchronization failed")


class CustomerProfile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="customer_profile",
        related_query_name="customer_profile",
        verbose_name=_("user"),
        help_text=_("The authenticated user that owns this customer profile"),
    )

    toconline_customer_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        unique=True,
        db_index=True,
        verbose_name=_("Customer ID (toconline)"),
        help_text=_("The ID of the customer in the toconline platform (invoice system)"),
    )
    billing_phone = models.CharField(max_length=30, blank=True)
    tax_number = models.CharField(max_length=30, blank=True)
    toconline_sync_status = models.CharField(
        max_length=20,
        choices=CustomerSyncStatus.choices,
        default=CustomerSyncStatus.PENDING,
        db_index=True,
    )
    toconline_sync_error = models.TextField(blank=True)
    toconline_sync_attempted_at = models.DateTimeField(null=True, blank=True)
    toconline_synced_at = models.DateTimeField(null=True, blank=True)

    @property
    def has_toconline_customer(self):
        return bool(self.toconline_customer_id)

    @property
    def phone_number(self):
        return self.billing_phone or None

    def __str__(self):
        return self.user.get_full_name() or f"{self.user.username} · {self.user.email}"
