import logging

from django.core.management.base import BaseCommand, CommandError

from hotel.services.catalog import CatalogSyncError, TocOnlineCatalogSynchronizer

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Publish website catalog items and reconcile their TOConline projections"

    def handle(self, *args, **options):
        try:
            result = TocOnlineCatalogSynchronizer().synchronize()
        except CatalogSyncError as exc:
            raise CommandError(str(exc)) from exc
        except Exception as exc:
            logger.exception("Unexpected TOConline catalog synchronization failure")
            raise CommandError(
                "TOConline catalog synchronization failed; consult the logs."
            ) from exc

        self.stdout.write(
            self.style.SUCCESS(
                "TOConline catalog synchronized: "
                f"{result.created} created, "
                f"{result.updated} updated, "
                f"{result.deactivated} deactivated, "
                f"{result.published} published, "
                f"{result.diverged} divergent."
            )
        )
