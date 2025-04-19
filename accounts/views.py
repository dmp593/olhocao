from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import render, redirect
from django.urls import reverse_lazy
from django.views.generic import UpdateView, TemplateView
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin

from django.contrib.auth.views import (
    LoginView,
    LogoutView,
    PasswordResetView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetCompleteView,
    PasswordChangeView,
    PasswordChangeDoneView,
)


from olhocao.toconline import toconline

from accounts.forms import UserChangeForm
from accounts.models import Account


def signup_view(request):
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            toconline.authenticate()

            # TODO
            # if toconline customer exists, fetch id from email...
            # else create customer in toconline

            user = form.save()
            Account(user=user).save()
            login(request, user)
            return redirect("frontoffice:home")
    else:
        form = UserCreationForm()
    return render(request, "accounts/signup.html", {"form": form})


class UserChangeView(LoginRequiredMixin, UpdateView):
    form_class = UserChangeForm
    template_name = "accounts/user_form.html"
    model = get_user_model()
    success_url = reverse_lazy("accounts:user_change_done")

    def get_object(self, queryset=...):
        return self.request.user


class UserChangeDoneView(LoginRequiredMixin, TemplateView):
    template_name = "accounts/user_change_done.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Account Updated")
        context["message"] = _(
            "Your account information has been successfully updated."
        )
        return context
