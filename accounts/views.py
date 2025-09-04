from django.contrib import messages
from django.contrib.auth import login
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


from toconline.services import toconline, TocOnlineResource

import logging

from accounts.forms import SignUpForm, UserChangeForm


def signup_view(request):
    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()

            try:
                ensure_toconline_customer(
                    user,
                    vat=form.cleaned_data.get('vat'),
                    phone=form.cleaned_data.get('phone'),
                )
            except Exception:  # noqa: BLE001 - external service: failure is non-critical
                logging.exception(
                    "TocOnline sync during signup failed for user %s", user.id
                )

            # Account is already created by SignUpForm.save(). Ensure TocOnline
            # customer is synced but don't block signup on TocOnline failures.
            login(request, user)

            success_msg = _("Welcome, %s", user.get_full_name())
            messages.success(request, success_msg)

            return redirect("frontoffice:home")
    else:
        form = SignUpForm()
    return render(request, "accounts/signup.html", {"form": form})


def ensure_toconline_customer(user, vat=None, phone=None):
    """Ensure a TocOnline customer exists for ``user.account``.

    Centralizes the logic used to find/create/update a TocOnline customer.
    """
    # require account relationship
    if user.account is None:
        return

    # try to find or create/update a toconline customer
    toconline_customer = None

    if not user.account.has_toconline_customer:
        # try different lookups using provided data
        if vat:
            toconline_customer = toconline.first(
                TocOnlineResource.CUSTOMERS,
                tax_registration_number=vat,
            )

        if not toconline_customer:
            toconline_customer = toconline.first(
                TocOnlineResource.CUSTOMERS,
                email=user.email,
            )

        if not toconline_customer and phone:
            toconline_customer = toconline.first(
                TocOnlineResource.CUSTOMERS,
                mobile_number=phone,
            )

        if not toconline_customer and phone:
            toconline_customer = toconline.first(
                TocOnlineResource.CUSTOMERS,
                phone_number=phone,
            )

        if not toconline_customer:
            toconline_customer = toconline.create(
                TocOnlineResource.CUSTOMERS,
                business_name=user.get_full_name(),
                contact_name=user.get_full_name(),
                email=user.email,
                mobile_number=phone,
                internal_observations='Created by olhocao.pt',
                tax_registration_number=vat,
            )
        else:
            toconline.update(
                TocOnlineResource.CUSTOMERS,
                pk=toconline_customer['id'],
                business_name=user.get_full_name(),
                contact_name=user.get_full_name(),
                email=user.email,
                mobile_number=phone,
                tax_registration_number=vat,
            )

        # attach and save
        user.account.toconline_customer_id = toconline_customer['id']
        user.account.save()
    else:
        # already has customer: update it
        toconline.update(
            TocOnlineResource.CUSTOMERS,
            pk=user.account.toconline_customer_id,
            business_name=user.get_full_name(),
            contact_name=user.get_full_name(),
            email=user.email,
            mobile_number=phone,
            tax_registration_number=vat,
        )


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

        if hasattr(user, 'account'):
            if user.account and user.account.has_toconline_customer:
                toconline_customer = user.account.toconline_customer
                customer_attrs = toconline_customer.get('attributes', {})

                data['vat'] = customer_attrs.get('tax_registration_number')
                data['phone'] = customer_attrs.get('mobile_number')

        return data

    def form_valid(self, form):
        # Save the user instance first
        user_obj = form.save()

        # Ensure toconline customer is created/updated using the
        # centralized helper. We pass cleaned data for vat/phone.
        ensure_toconline_customer(
            user_obj,
            vat=form.cleaned_data.get('vat'),
            phone=form.cleaned_data.get('phone'),
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
