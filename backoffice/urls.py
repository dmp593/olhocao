from django.urls import path

from backoffice import views


app_name = 'backoffice'


urlpatterns = [
    path('dashboard/', views.DashboardView.as_view(), name='dashboard'),
]
