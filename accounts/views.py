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


from olhocao.toconline import toconline, TocOnlineResource

from accounts.forms import UserChangeForm
from accounts.models import Account


def signup_view(request):
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            toconline.authenticate()
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

    def get_initial(self):
        user = self.get_object()
        data = super().get_initial()

        if user.account and user.account.has_toconline_customer:
            toconline_customer = user.account.toconline_customer

            data['vat'] = toconline_customer['attributes']['tax_registration_number']
            data['phone'] = toconline_customer['attributes']['mobile_number']

        return data

    def form_valid(self, form):
        self.object = form.save()
        toconline_customer = None

        if not self.object.account.has_toconline_customer:
            toconline_customer = toconline.first(
                TocOnlineResource.CUSTOMERS,
                tax_registration_number=form.cleaned_data['vat']
            )

            if not toconline_customer:
                toconline_customer = toconline.first(
                    TocOnlineResource.CUSTOMERS,
                    email=self.object.email
                )

            if not toconline_customer:
                toconline_customer = toconline.first(
                    TocOnlineResource.CUSTOMERS,
                    mobile_number=form.cleaned_data['phone']
                )

            if not toconline_customer:
                toconline_customer = toconline.first(
                    TocOnlineResource.CUSTOMERS,
                    phone_number=form.cleaned_data['phone']
                )

            if not toconline_customer:
                toconline_customer = toconline.create(
                    TocOnlineResource.CUSTOMERS,
                    business_name=self.object.get_full_name(),
                    contact_name=self.object.get_full_name(),
                    email=self.object.email,
                    mobile_number=form.cleaned_data['phone'],
                    internal_observations='Created by olhocao.pt',
                    tax_registration_number=form.cleaned_data['vat']
                )
            else:
                toconline.update(
                    TocOnlineResource.CUSTOMERS,
                    pk=toconline_customer['id'],
                    business_name=self.object.get_full_name(),
                    contact_name=self.object.get_full_name(),
                    email=self.object.email,
                    mobile_number=form.cleaned_data['phone'],
                    tax_registration_number=form.cleaned_data['vat']
                )

            self.object.account.toconline_customer_id = toconline_customer['id']
            self.object.account.save()
        else:
            toconline.update(
                TocOnlineResource.CUSTOMERS,
                pk=self.object.account.toconline_customer_id,
                business_name=self.object.get_full_name(),
                contact_name=self.object.get_full_name(),
                email=self.object.email,
                mobile_number=form.cleaned_data['phone'],
                tax_registration_number=form.cleaned_data['vat']
            )

        return super().form_valid(form)


class UserChangeDoneView(LoginRequiredMixin, TemplateView):
    template_name = "accounts/user_change_done.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Account Updated")
        context["message"] = _(
            "Your account information has been successfully updated."
        )
        return context
