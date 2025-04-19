from django.urls import path
from . import views


app_name = 'hotel'


urlpatterns = [
    path('bookings/', views.HotelBookingListView.as_view(), name='booking_list'),
    path('bookings/<int:pk>/', views.HotelBookingDetailView.as_view(), name='booking_detail'),

    path('book-now/', views.BookingStayListView.as_view(), name='booking_stay'),
    path('book-now/services/', views.BookingStayServiceListView.as_view(), name='booking_services'),
    path('book-now/review/', views.BookingReviewView.as_view(), name='booking_review'),
    path('book-now/confirm/', views.BookingConfirmView.as_view(), name='booking_confirm'),
    path('book-now/verify/<int:booking_id>/', views.BookingPaymentVerifyView.as_view(), name='booking_payment_verify'),
    path('book-now/success/<int:booking_id>/', views.BookingSuccessView.as_view(), name='booking_success'),
    path('book-now/retry/<int:booking_id>/', views.BookingRetryView.as_view(), name='booking_retry'),
]
