from datetime import timedelta
from django.utils.dateparse import parse_date
from django.views.generic import TemplateView
from django.utils import timezone
from hotel.models import BookingStay


class DashboardView(TemplateView):
    template_name = 'backoffice/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.now().date()

        # Get and validate dates from request
        start_date = parse_date(self.request.GET.get('start_date', today.isoformat()))
        end_date = parse_date(self.request.GET.get('end_date', today.isoformat()))
        
        if start_date > end_date:
            start_date, end_date = end_date, start_date

        # Calculate navigation dates
        duration = (end_date - start_date).days + 1
        previous_start = start_date - timedelta(days=duration)
        previous_end = previous_start + (end_date - start_date)
        next_start = start_date + timedelta(days=duration)
        next_end = next_start + (end_date - start_date)

        # Get stays overlapping with date range
        stays = BookingStay.objects.filter(
            start_date__lte=end_date,
            end_date__gte=start_date
        ).select_related('booking', 'pet').order_by('start_date')

        context.update({
            'start_date': start_date,
            'end_date': end_date,
            'checkins': stays.filter(start_date__range=[start_date, end_date]),
            'checkouts': stays.filter(end_date__range=[start_date, end_date]),
            'stays': stays,
            'previous_start': previous_start,
            'previous_end': previous_end,
            'next_start': next_start,
            'next_end': next_end,
        })
        return context
