from django.db.models import Q
from django.utils import timezone

from hotel.models import CONFIRMED_BOOKING_STATUSES, BookingStay


def filtered_booking_stays(filters):
    queryset = (
        BookingStay.objects.select_related(
            "booking",
            "booking__customer",
            "booking__customer__user",
            "pet",
            "catalog_item",
        )
        .prefetch_related("services")
        .order_by("start_date", "pet__name", "id")
    )

    query = filters.get("q")
    if query:
        queryset = queryset.filter(
            Q(booking__customer__user__first_name__icontains=query)
            | Q(booking__customer__user__last_name__icontains=query)
            | Q(booking__customer__user__email__icontains=query)
            | Q(booking__customer__billing_phone__icontains=query)
            | Q(booking__customer__tax_number__icontains=query)
            | Q(pet__name__icontains=query)
        )

    start_date = filters.get("start_date")
    end_date = filters.get("end_date")
    date_mode = filters.get("date_mode") or "overlap"
    if start_date or end_date:
        start_date = start_date or end_date
        end_date = end_date or start_date
        if date_mode == "checkin":
            queryset = queryset.filter(start_date__range=(start_date, end_date))
        elif date_mode == "checkout":
            queryset = queryset.filter(end_date__range=(start_date, end_date))
        else:
            queryset = queryset.filter(start_date__lte=end_date, end_date__gte=start_date)

    if filters.get("booking_status"):
        queryset = queryset.filter(booking__status=filters["booking_status"])
    if filters.get("payment_status"):
        queryset = queryset.filter(booking__payment_attempts__status=filters["payment_status"])
    if filters.get("document_status"):
        queryset = queryset.filter(booking__document_status=filters["document_status"])

    medical = filters.get("medical")
    if medical == "medication":
        queryset = queryset.exclude(pet__medication="")
    elif medical == "allergies":
        queryset = queryset.exclude(pet__allergies="")
    elif medical == "notes":
        queryset = queryset.exclude(pet__notes="")

    if filters.get("accommodation"):
        queryset = queryset.filter(catalog_item=filters["accommodation"])
    if filters.get("extra"):
        queryset = queryset.filter(services__catalog_item=filters["extra"])
    return queryset.distinct()


def operational_stays(today=None):
    today = today or timezone.localdate()
    return BookingStay.objects.filter(
        booking__status__in=CONFIRMED_BOOKING_STATUSES,
        start_date__lte=today,
        end_date__gt=today,
    ).select_related("booking", "booking__customer__user", "pet")
