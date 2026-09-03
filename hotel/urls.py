from django.urls import path

from . import booking_views, media_views

app_name = "hotel"


urlpatterns = [
    path("book-now/", booking_views.BookingStayListView.as_view(), name="booking_stay"),
    path(
        "book-now/services/",
        booking_views.BookingStayServiceListView.as_view(),
        name="booking_services",
    ),
    path("book-now/review/", booking_views.BookingReviewView.as_view(), name="booking_review"),
    path("book-now/confirm/", booking_views.BookingConfirmView.as_view(), name="booking_confirm"),
    path("bookings/", booking_views.HotelBookingListView.as_view(), name="booking_list"),
    path(
        "bookings/<int:pk>/",
        booking_views.HotelBookingDetailView.as_view(),
        name="booking_detail",
    ),
    path(
        "bookings/<int:booking_id>/verify/",
        booking_views.BookingPaymentVerifyView.as_view(),
        name="booking_payment_verify",
    ),
    path(
        "bookings/<int:booking_id>/success/",
        booking_views.BookingSuccessView.as_view(),
        name="booking_success",
    ),
    path(
        "bookings/<int:booking_id>/retry/",
        booking_views.BookingRetryView.as_view(),
        name="booking_retry",
    ),
    path(
        "bookings/<int:booking_id>/modify/",
        booking_views.HotelBookingModifyView.as_view(),
        name="booking_modify",
    ),
    path(
        "bookings/<int:booking_id>/price/",
        booking_views.BookingPriceAdjustmentView.as_view(),
        name="booking_price_adjustment",
    ),
    path(
        "bookings/<int:booking_id>/refund/",
        booking_views.BookingRefundView.as_view(),
        name="booking_refund",
    ),
    path(
        "bookings/<int:booking_id>/refunds/<int:refund_id>/retry/",
        booking_views.BookingRefundRetryView.as_view(),
        name="booking_refund_retry",
    ),
    path(
        "bookings/<int:booking_id>/refunds/<int:refund_id>/pdf/",
        booking_views.download_booking_refund_pdf,
        name="booking_refund_pdf",
    ),
    path(
        "bookings/<int:booking_id>/cancel/",
        booking_views.HotelBookingCancelConfirmView.as_view(),
        name="booking_cancel_confirm",
    ),
    path(
        "bookings/<int:booking_id>/cancel/confirm/",
        booking_views.HotelBookingCancelView.as_view(),
        name="booking_cancel",
    ),
    path(
        "bookings/<int:booking_id>/pdf/",
        booking_views.download_sales_document_pdf,
        name="booking_pdf",
    ),
    path("webhooks/easypay/", booking_views.EasypayWebhookView.as_view(), name="easypay_webhook"),
    # Stay media
    path(
        "stays/<int:pk>/media/upload-chunk/",
        media_views.StayMediaUploadChunkView.as_view(),
        name="stay_media_upload",
    ),
    path(
        "stays/<int:pk>/media/<int:media_id>/",
        media_views.StayMediaDownloadView.as_view(),
        name="stay_media_download",
    ),
    path(
        "stays/<int:pk>/media/<int:media_id>/delete/",
        media_views.StayMediaDeleteView.as_view(),
        name="stay_media_delete",
    ),
]
