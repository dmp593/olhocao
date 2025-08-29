from django.urls import path
from . import views


app_name = 'hotel'


urlpatterns = [
    path('book-now/', views.BookingStayListView.as_view(), name='booking_stay'),
    path('book-now/services/', views.BookingStayServiceListView.as_view(), name='booking_services'),
    path('book-now/review/', views.BookingReviewView.as_view(), name='booking_review'),
    path('book-now/confirm/', views.BookingConfirmView.as_view(), name='booking_confirm'),

    path('bookings/', views.HotelBookingListView.as_view(), name='booking_list'),
    path('bookings/<int:pk>/', views.HotelBookingDetailView.as_view(), name='booking_detail'),
    path('bookings/<int:booking_id>/verify/', views.BookingPaymentVerifyView.as_view(), name='booking_payment_verify'),
    path('bookings/<int:booking_id>/success/', views.BookingSuccessView.as_view(), name='booking_success'),
    path('bookings/<int:booking_id>/retry/', views.BookingRetryView.as_view(), name='booking_retry'),
    path('bookings/<int:booking_id>/modify/', views.HotelBookingModifyView.as_view(), name='booking_modify'),
    path('bookings/<int:booking_id>/cancel/', views.HotelBookingCancelConfirmView.as_view(), name='booking_cancel_confirm'),
    path('bookings/<int:booking_id>/cancel/confirm/', views.HotelBookingCancelView.as_view(), name='booking_cancel'),
    path('bookings/<int:booking_id>/pdf/', views.download_sales_document_pdf, name='booking_pdf'),
    # Stay media
    path(
        'stays/<int:pk>/media/upload-chunk/',
        views.StayMediaUploadChunkView.as_view(),
        name='stay_media_upload'
    ),
    path(
        'stays/<int:pk>/media/<int:media_id>/delete/',
        views.StayMediaDeleteView.as_view(),
        name='stay_media_delete'
    ),
]
