import logging

from django.db import OperationalError, ProgrammingError

from accounts.models import CustomerProfile
from frontoffice.models import ContactRequest

logger = logging.getLogger(__name__)


def unread_contact_requests_count(request):
    """Provide unread contact requests count to templates (staff only)."""
    try:
        user = getattr(request, "user", None)
        if user and user.is_authenticated and user.is_staff:
            count = ContactRequest.objects.filter(read_at__isnull=True).count()
            return {"unread_contact_requests_count": count}
    except (OperationalError, ProgrammingError):
        logger.warning(
            "Could not calculate unread contact request count",
            exc_info=True,
        )
    return {"unread_contact_requests_count": 0}


def assisted_customer(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not user.is_staff:
        return {"assisted_customer": None}
    customer_id = request.session.get("assisted_customer_id")
    if not customer_id:
        return {"assisted_customer": None}
    customer = (
        CustomerProfile.objects.select_related("user")
        .filter(pk=customer_id, user__is_active=True, user__is_staff=False)
        .first()
    )
    return {"assisted_customer": customer}
