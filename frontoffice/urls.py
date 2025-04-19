from django.urls import path

from frontoffice import views


app_name = 'frontoffice'


urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    path('pricing/', views.PricingView.as_view(), name='pricing'),
    path('contact-us/', views.ContactUsView.as_view(), name='contact_us'),
    path('about-us/', views.AboutUsView.as_view(), name='about_us'),
]
