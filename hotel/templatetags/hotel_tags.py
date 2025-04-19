from django import template
from django.utils import timezone


register = template.Library()


@register.filter
def is_past(booking):
    now = timezone.now().date()
    return any(stay.end_date < now for stay in booking.stays.all())

@register.filter
def is_active(booking):
    now = timezone.now().date()
    return (booking.status == 'paid' and 
            any(stay.start_date <= now <= stay.end_date for stay in booking.stays.all()))

@register.filter
def is_upcoming(booking):
    now = timezone.now().date()
    return (booking.status == 'paid' and 
            all(stay.start_date > now for stay in booking.stays.all()))