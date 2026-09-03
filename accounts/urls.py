from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views

app_name = "accounts"


urlpatterns = [
    path("", views.CustomerDashboardView.as_view(), name="dashboard"),
    path("change/", views.UserChangeView.as_view(), name="user_change"),
    path("change/done/", views.UserChangeDoneView.as_view(), name="user_change_done"),
    path(
        "login/",
        views.RoleAwareLoginView.as_view(),
        name="login",
    ),
    path(
        "logout/",
        auth_views.LogoutView.as_view(
            template_name="accounts/logged_out.html",
        ),
        name="logout",
    ),
    path("signup/", views.signup_view, name="signup"),
    path(
        "password-reset/",
        auth_views.PasswordResetView.as_view(
            template_name="accounts/password_reset_form.html",
            success_url=reverse_lazy("accounts:password_reset_done"),
        ),
        name="password_reset",
    ),
    path(
        "password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="accounts/password_reset_done.html",
        ),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_reset_confirm.html",
            success_url=reverse_lazy("accounts:password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "reset/done/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="accounts/password_reset_complete.html",
        ),
        name="password_reset_complete",
    ),
    path(
        "password-change/",
        auth_views.PasswordChangeView.as_view(
            template_name="accounts/password_change_form.html",
            success_url=reverse_lazy("accounts:password_change_done"),
        ),
        name="password_change",
    ),
    path(
        "password-change/done/",
        auth_views.PasswordChangeDoneView.as_view(
            template_name="accounts/password_change_done.html",
        ),
        name="password_change_done",
    ),
]
