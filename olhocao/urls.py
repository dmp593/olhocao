"""
URL configuration for olhocao project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from .views import liveness, readiness

urlpatterns = [
    path(
        "favicon.ico",
        RedirectView.as_view(url=f"{settings.STATIC_URL}paws.svg", permanent=True),
        name="favicon",
    ),
    path("", include("frontoffice.urls")),
    path("backoffice/", include("backoffice.urls")),
    path("backoffice/promotions/", include("discounts.urls")),
    path("accounts/", include("accounts.urls")),
    path("pets/", include("pets.urls")),
    path("hotel/", include("hotel.urls")),
    path("activities/", include("activities.urls")),
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("health/live/", liveness, name="health_live"),
    path("health/ready/", readiness, name="health_ready"),
]

if settings.DEBUG:
    urlpatterns += [
        path("__reload__/", include("django_browser_reload.urls")),
    ]
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
