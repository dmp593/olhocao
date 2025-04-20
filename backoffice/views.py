from datetime import timedelta
from django.utils import timezone
from django.views import View
from django.shortcuts import render

from hotel.models import BookingStay


class DashboardView(View):
    def get(self, request):
        today = timezone.now().date()
        dates = [today + timedelta(days=i) for i in range(-3, 11)]  # 14-day window
        
        # Day aggregates
        days = []
        queryset = BookingStay.objects

        for date in dates:
            checkins = queryset.filter(start_date=date).count()
            checkouts = queryset.filter(end_date=date).count()

            stayings = queryset.filter(
                start_date__lt=date,
                end_date__gt=date
            ).count()
            
            days.append({
                'date': date,
                'checkins': checkins,
                'checkouts': checkouts,
                'stayings': stayings
            })
        
        context = {
            'days': days,
            'current_stays': BookingStay.objects.current().all(),
            'upcoming_events': {
                'checkins': BookingStay.objects.filter(
                    start_date__gte=today,
                    start_date__lte=today + timedelta(days=1)
                ),
                'checkouts': BookingStay.objects.filter(
                    end_date__gte=today,
                    end_date__lte=today + timedelta(days=1)
                )
            }
        }

        return render(request, 'backoffice/dashboard.html', context)
