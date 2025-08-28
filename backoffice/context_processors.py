from frontoffice.models import ContactRequest


def unread_contact_requests_count(request):
    """Provide unread contact requests count to templates (staff only)."""
    try:
        user = getattr(request, 'user', None)
        if user and user.is_authenticated and user.is_staff:
            count = ContactRequest.objects.filter(read_at__isnull=True).count()
            return {"unread_contact_requests_count": count}
    except Exception:
        # Be resilient in templates; on any error, hide badge.
        pass
    return {"unread_contact_requests_count": 0}
