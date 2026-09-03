import logging

from django.core.management.base import BaseCommand

from accounts.models import CustomerProfile, CustomerSyncStatus
from accounts.services.toconline_customers import synchronize_customer

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Publish pending local customers to TOConline without blocking web requests"

    def handle(self, *args, **options):
        synchronized = 0
        failed = 0
        profiles = CustomerProfile.objects.filter(
            toconline_sync_status__in=(
                CustomerSyncStatus.PENDING,
                CustomerSyncStatus.FAILED,
            )
        )
        for profile in profiles.iterator():
            try:
                synchronize_customer(profile)
            except Exception:
                failed += 1
                logger.exception(
                    "TOConline customer synchronization remains pending",
                    extra={"customer_profile_id": profile.pk},
                )
            else:
                synchronized += 1

        self.stdout.write(f"Synchronized: {synchronized}; still failed: {failed}")
