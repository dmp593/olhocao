from django.http import HttpResponseRedirect
from django.views.generic import (
    ListView,
    DetailView,
    CreateView,
    UpdateView,
    DeleteView,
)
from django.views import View
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone

from accounts.models import Account
from .models import Pet
from .forms import PetForm, PetAdminForm


class PetListView(LoginRequiredMixin, ListView):
    model = Pet
    context_object_name = "pets"

    def get_queryset(self):
        if self.request.user.is_staff:
            status = self.request.GET.get("status", "all")

            qs = Pet.objects.all()

            if status == "active":
                qs = qs.filter(deleted_at__isnull=True)
            elif status == "deleted":
                qs = qs.filter(deleted_at__isnull=False)

            return qs

        return (
            Pet.objects
            .filter(owner__user=self.request.user, deleted_at__isnull=True)
            .all()
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_staff:
            context["status"] = self.request.GET.get("status", "all")
        return context


class PetDetailView(LoginRequiredMixin, DetailView):
    model = Pet
    context_object_name = "pet"

    def get_queryset(self):
        if self.request.user.is_staff:
            return Pet.objects.all()

        return (
            Pet.objects
            .filter(
                owner__user=self.request.user, deleted_at__isnull=True
            )
            .all()
        )


class PetCreateView(LoginRequiredMixin, CreateView):
    model = Pet
    form_class = PetForm
    success_url = reverse_lazy("pets:list")

    def form_valid(self, form):
        # If staff and using admin form, owner comes from the form
        if self.request.user.is_staff and isinstance(form, PetAdminForm):
            pass
        else:
            form.instance.owner = self.request.user.account
        return super().form_valid(form)

    def get_form_class(self):
        if self.request.user.is_staff:
            return PetAdminForm
        return PetForm

    def get_initial(self):
        initial = super().get_initial()
        if self.request.user.is_staff:
            owner_user_id = (
                self.request.GET.get('owner')
                or self.request.session.get('acting_user_id')
            )
            if owner_user_id:
                try:
                    initial['owner'] = Account.objects.get(
                        user_id=owner_user_id
                    )
                except Account.DoesNotExist:
                    pass
        return initial

    def get_success_url(self):
        if (
            self.request.user.is_staff
            and self.request.session.get('acting_user_id')
        ):
            return reverse_lazy('hotel:booking_stay')
        return super().get_success_url()


class PetUpdateView(LoginRequiredMixin, UpdateView):
    model = Pet
    form_class = PetForm
    success_url = reverse_lazy("pets:list")

    def get_queryset(self):
        queryset = Pet.objects.all()

        if self.request.user.is_staff:
            return queryset
        return queryset.filter(
            owner__user=self.request.user,
            deleted_at__isnull=True,
        )

    def get_form_class(self):
        # Allow staff to reassign owner if needed
        if self.request.user.is_staff:
            return PetAdminForm
        return PetForm


class PetDeleteView(LoginRequiredMixin, DeleteView):
    model = Pet
    success_url = reverse_lazy("pets:list")

    def get_queryset(self):
        queryset = Pet.objects.all()

        if self.request.user.is_staff:
            return queryset

        return queryset.filter(
            owner__user=self.request.user,
            deleted_at__isnull=True,
        )

    def form_valid(self, form):
        success_url = self.get_success_url()

        # This is a soft delete approach.

        # Instead of deleting the object:
        #   self.object.delete()

        # We mark it as deleted, and save the deletion timestamp.
        self.object.deleted_at = timezone.now()
        self.object.save()

        return HttpResponseRedirect(success_url)


class PetRestoreView(LoginRequiredMixin, View):
    """Restore a soft-deleted pet (staff only)."""

    def post(self, request, pk):
        if not request.user.is_staff:
            return HttpResponseRedirect(reverse_lazy("pets:list"))

        try:
            pet = Pet.objects.get(pk=pk)
        except Pet.DoesNotExist:
            return HttpResponseRedirect(reverse_lazy("pets:list"))

        pet.deleted_at = None
        pet.save(update_fields=["deleted_at"])

        return HttpResponseRedirect(reverse_lazy("pets:list"))
