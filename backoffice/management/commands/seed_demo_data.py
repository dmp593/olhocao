from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from backoffice.services.demo_data import create_demo_data


class Command(BaseCommand):
    help = "Create deterministic data in an explicitly enabled local demo database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            help=(
                "Password for every demo login. Prefer DEMO_DATA_PASSWORD so the value "
                "does not remain in shell history."
            ),
        )

    def handle(self, *args, **options):
        try:
            summary = create_demo_data(password=options["password"])
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS("Local demo data is ready."))
        self.stdout.write(
            "Created/available: "
            f"{summary.users} users, {summary.customer_profiles} customer profiles, "
            f"{summary.dogs} dogs, {summary.catalog_types} catalog types, "
            f"{summary.offers} prices/plans, {summary.entitlements} purchases, "
            f"{summary.session_series} schedules, {summary.sessions} sessions, "
            f"{summary.reservations} reservations and "
            f"{summary.hotel_bookings} hotel bookings."
        )
        self.stdout.write("Logins: demo_admin, demo_manager, demo_ana, demo_bruno")
        self.stdout.write(
            "Password: DEMO_DATA_PASSWORD, --password, or the documented local default."
        )
