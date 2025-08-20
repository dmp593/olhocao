from uuid import UUID, uuid4

from django.conf import settings
from django.core.management.base import BaseCommand
from djstripe.models import WebhookEndpoint, Account


class Command(BaseCommand):
    help = "Creates a local Webhook to use Stripe for development"

    def add_arguments(self, parser):
        parser.add_argument(
            "uuid",
            type=UUID,
            nargs="?",
            default=uuid4(),
            help="The djstripe UUID"
        )
        parser.add_argument(
            "stripe_webhook_secret",
            type=str,
            nargs="?",
            default=settings.DJSTRIPE_WEBHOOK_SECRET,
            help="Stripe Webhook Secret"
        )

        parser.add_argument(
            "stripe_account",
            type=str,
            nargs="?",
            default=Account.objects.first().id,
            help="Stripe Account"
        )

    def handle(self, *args, **kwargs):
        webhook_uuid = kwargs.get("uuid")
        stripe_webhook_secret = kwargs.get("stripe_webhook_secret")
        stripe_account = Account.objects.get(id=kwargs.get("stripe_account"))

        WebhookEndpoint.objects.create(
            djstripe_uuid=webhook_uuid,
            secret=stripe_webhook_secret,
            djstripe_owner_account=stripe_account,
            url=f"http://localhost:8000/stripe/webhook/{webhook_uuid}/",
            enabled_events=["*"],
            livemode=False,
        )

        self.stdout.write(
            f"Stripe Webhook Endpoint created with uuid {webhook_uuid}."
        )
