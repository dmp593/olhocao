from datetime import timedelta
from django.utils import timezone
from django.views import View
from django.shortcuts import render

from hotel.models import BookingStay


class DashboardView(View):
    def get(self, request):
        today = timezone.now().date()
        date_range = [today + timedelta(days=i) for i in range(-3, 11)]  # 14-day window
        
        # Day aggregates
        days = []
        for date in date_range:
            checkins = BookingStay.objects.filter(start_date=date).count()
            checkouts = BookingStay.objects.filter(end_date=date).count()
            current = BookingStay.objects.filter(
                start_date__lte=date, 
                end_date__gte=date
            ).count()
            days.append({
                'date': date,
                'checkins': checkins,
                'checkouts': checkouts,
                'current': current
            })
        
        # Current stays
        current_stays = BookingStay.objects.filter(
            start_date__lte=today,
            end_date__gte=today
        ).select_related('pet', 'booking__account')[:20]
        
        # Upcoming events
        upcoming_events = []
        checkins = BookingStay.objects.filter(
            start_date__gte=today,
            start_date__lte=today + timedelta(days=1)
        ).select_related('pet', 'booking__account')
        
        checkouts = BookingStay.objects.filter(
            end_date__gte=today,
            end_date__lte=today + timedelta(days=1)
        ).select_related('pet', 'booking__account')
        
        for stay in checkins:
            upcoming_events.append({
                'type': 'checkin',
                'time': stay.start_date,
                'pet_name': stay.pet.name,
                'owner_name': stay.booking.account.user.get_full_name()
            })
        
        for stay in checkouts:
            upcoming_events.append({
                'type': 'checkout',
                'time': stay.end_date,
                'pet_name': stay.pet.name,
                'owner_name': stay.booking.account.user.get_full_name()
            })
        
        context = {
            'days': days,
            'current_stays': current_stays,
            'upcoming_events': sorted(upcoming_events, key=lambda x: x['time'])[:10],
            'current_count': BookingStay.objects.current().count()
        }

        return render(request, 'backoffice/dashboard.html', context)
