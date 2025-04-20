from django.utils import timezone
from olhocao import managers


class BookingManager(managers.Manager):
    def current(self):
        today = timezone.now().date()

        return self.get_queryset().filter(
            stay__start_date__lte=today,
            stay__end_date__gte=today
        ).all()


class BookingStayManager(managers.Manager):
    def current(self):
        today = timezone.now().date()

        return self.get_queryset().filter(
            start_date__lte=today,
            end_date__gte=today
        ).all()
