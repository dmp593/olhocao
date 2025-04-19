from django.http import HttpResponseRedirect
from django.views.generic import (
    ListView,
    DetailView,
    CreateView,
    UpdateView,
    DeleteView,
)
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils.translation import gettext_lazy as _
from django.utils import timezone

from .models import Pet
from .forms import PetForm


class PetListView(LoginRequiredMixin, ListView):
    model = Pet
    context_object_name = "pets"

    def get_queryset(self):
        return Pet.objects.filter(owner__user=self.request.user)


class PetDetailView(LoginRequiredMixin, DetailView):
    model = Pet
    context_object_name = "pet"

    def get_queryset(self):
        return Pet.objects.filter(owner__user=self.request.user)


class PetCreateView(LoginRequiredMixin, CreateView):
    model = Pet
    form_class = PetForm
    success_url = reverse_lazy("pets:list")

    def form_valid(self, form):
        form.instance.owner = self.request.user.account
        return super().form_valid(form)


class PetUpdateView(LoginRequiredMixin, UpdateView):
    model = Pet
    form_class = PetForm
    success_url = reverse_lazy("pets:list")

    def get_queryset(self):
        return Pet.objects.filter(owner__user=self.request.user)


class PetDeleteView(LoginRequiredMixin, DeleteView):
    model = Pet
    success_url = reverse_lazy("pets:list")

    def get_queryset(self):
        return Pet.objects.filter(owner__user=self.request.user)

    def form_valid(self, form):
        success_url = self.get_success_url()

        # This is a soft delete approach.

        # Instead of deleting the object:
        #   self.object.delete()

        # We mark it as deleted, and save the deletion timestamp.
        self.object.deleted_at = timezone.now()
        self.object.save()

        return HttpResponseRedirect(success_url)
