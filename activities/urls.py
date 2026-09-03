from django.urls import path

from activities import views

app_name = "activities"

urlpatterns = [
    path("", views.OfferingListView.as_view(), name="offering_list"),
    path("my/", views.ActivityPortalView.as_view(), name="portal"),
    path("my/documents/", views.ActivityDocumentListView.as_view(), name="documents"),
    path(
        "documents/subscription-cycles/<int:pk>/pdf/",
        views.ActivityDocumentDownloadView.as_view(),
        name="subscription_cycle_pdf",
    ),
    path(
        "documents/subscription-cycles/<int:pk>/credit-note/",
        views.ActivityCreditNoteDownloadView.as_view(),
        name="subscription_cycle_credit_pdf",
    ),
    path(
        "documents/purchases/<int:pk>/pdf/",
        views.PurchaseDocumentDownloadView.as_view(),
        name="purchase_document_pdf",
    ),
    path(
        "documents/purchase-refunds/<int:pk>/pdf/",
        views.PurchaseRefundDocumentDownloadView.as_view(),
        name="purchase_refund_document_pdf",
    ),
    path("sessions/", views.SessionListView.as_view(), name="session_list"),
    path("offers/<int:pk>/purchase/", views.PurchaseServiceView.as_view(), name="purchase"),
    path(
        "entitlements/<int:pk>/request-service/",
        views.FlexibleServiceRequestView.as_view(),
        name="request_fulfilment",
    ),
    path(
        "service-requests/<int:pk>/cancel/",
        views.CancelFlexibleServiceView.as_view(),
        name="cancel_fulfilment",
    ),
    path("sessions/<int:pk>/book/", views.ReserveSessionView.as_view(), name="reserve"),
    path(
        "reservations/<int:pk>/add-ons/",
        views.ActivityAddOnListView.as_view(),
        name="reservation_add_ons",
    ),
    path(
        "reservations/<int:reservation_pk>/add-ons/<int:offering_pk>/purchase/",
        views.ActivityAddOnPurchaseView.as_view(),
        name="purchase_reservation_add_on",
    ),
    path(
        "reservations/<int:pk>/cancel/",
        views.CancelReservationView.as_view(),
        name="cancel_reservation",
    ),
    path(
        "subscriptions/<int:pk>/cancel/",
        views.CancelSubscriptionView.as_view(),
        name="cancel_subscription",
    ),
    path("payments/<int:pk>/return/", views.PaymentReturnView.as_view(), name="payment_return"),
]
