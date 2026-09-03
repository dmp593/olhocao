from django.urls import path

from discounts import views

app_name = "discounts"

urlpatterns = [
    path("", views.PromotionListView.as_view(), name="promotion_list"),
    path(
        "audience-search/<str:kind>/",
        views.PromotionAudienceSearchView.as_view(),
        name="promotion_audience_search",
    ),
    path("create/", views.PromotionCreateView.as_view(), name="promotion_create"),
    path("<int:pk>/", views.PromotionUpdateView.as_view(), name="promotion_update"),
    path("<int:pk>/toggle/", views.PromotionToggleView.as_view(), name="promotion_toggle"),
]
