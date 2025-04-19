from django.urls import path, reverse_lazy
from . import views


app_name = "accounts"


urlpatterns = [
    path(
        'change/',
        views.UserChangeView.as_view(),
        name='user_change'
    ),

    path(
        'change/done/',
        views.UserChangeDoneView.as_view(),
        name='user_change_done'
    ),

    path(
        "login/",
        views.LoginView.as_view(
            template_name="accounts/login.html",
        ),
        name="login"
    ),

    path(
        "logout/",
        views.LogoutView.as_view(
            template_name="accounts/logged_out.html",
        ),
        name="logout"
    ),

    path(
        "signup/",
        views.signup_view,
        name="signup"
    ),

    path(
        "password-reset/",
        views.PasswordResetView.as_view(
            template_name="accounts/password_reset_form.html",
            success_url=reverse_lazy("accounts:password_reset_done"),
        ),
        name="password_reset",
    ),
    path(
        "password-reset/done/",
        views.PasswordResetDoneView.as_view(
            template_name="accounts/password_reset_done.html",
        ),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_reset_confirm.html",
            success_url=reverse_lazy("accounts:password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "reset/done/",
        views.PasswordResetCompleteView.as_view(
            template_name="accounts/password_reset_complete.html",
        ),
        name="password_reset_complete",
    ),
    path(
        "password-change/",
        views.PasswordChangeView.as_view(
            template_name="accounts/password_change_form.html",
            success_url=reverse_lazy("accounts:password_change_done"),
        ),
        name="password_change",
    ),
    path(
        "password-change/done/",
        views.PasswordChangeDoneView.as_view(
            template_name="accounts/password_change_done.html",
        ),
        name="password_change_done",
    ),
]
