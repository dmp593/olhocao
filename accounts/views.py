from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView, UpdateView

from accounts.forms import SignUpForm, UserChangeForm


class RoleAwareLoginView(LoginView):
    template_name = "accounts/login.html"

    def get_success_url(self):
        requested_destination = self.get_redirect_url()
        if requested_destination:
            return requested_destination
        if self.request.user.is_staff:
            return reverse("backoffice:dashboard")
        return reverse("accounts:dashboard")


def get_safe_auth_redirect(request):
    """Return a same-origin post-authentication destination, when supplied."""
    destination = request.POST.get("next") or request.GET.get("next")
    if destination and url_has_allowed_host_and_scheme(
        destination,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return destination
    return ""


def signup_view(request):
    next_url = get_safe_auth_redirect(request)
    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)

            success_msg = _("Welcome") + f", {user.get_full_name()}"
            messages.success(request, success_msg)

            return redirect(next_url or reverse("accounts:dashboard"))
    else:
        form = SignUpForm()
    return render(request, "accounts/signup.html", {"form": form, "next": next_url})


class UserChangeView(LoginRequiredMixin, UpdateView):
    form_class = UserChangeForm
    template_name = "accounts/user_form.html"
    model = get_user_model()
    success_url = reverse_lazy("accounts:user_change_done")

    def get_object(self, queryset=None):
        return self.request.user

    def get_initial(self):
        user = self.get_object()
        data = super().get_initial()

        if hasattr(user, "customer_profile"):
            data["vat"] = user.customer_profile.tax_number
            data["phone"] = user.customer_profile.billing_phone

        return data

    def form_valid(self, form):
        form.save()
        return redirect(self.get_success_url())


class UserChangeDoneView(LoginRequiredMixin, TemplateView):
    template_name = "accounts/user_change_done.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Account Updated")
        context["message"] = _("Your account information has been successfully updated.")
        return context


class CustomerDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "accounts/dashboard.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.is_staff:
            return redirect("backoffice:dashboard")
        if request.user.is_authenticated and not hasattr(request.user, "customer_profile"):
            messages.info(request, _("Complete your account details to continue."))
            return redirect("accounts:user_change")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        customer = self.request.user.customer_profile

        context.update(
            {
                "dogs": customer.pets.filter(deleted_at__isnull=True).order_by("name")[:4],
                "bookings": customer.bookings.prefetch_related("stays__pet").order_by(
                    "-created_at"
                )[:3],
                "services": customer.service_entitlements.select_related(
                    "pet", "offering__catalog_offer__catalog_item"
                ).order_by("-created_at")[:3],
            }
        )
        return context
