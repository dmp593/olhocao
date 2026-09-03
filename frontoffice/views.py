from typing import Any

from django.contrib import messages
from django.db.models import Prefetch, Q
from django.urls import reverse_lazy
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView

from backoffice.models import LegalDocument
from catalog.models import CatalogOffer, CatalogType, CatalogWorkflow

from . import forms
from .models import HomeSlide, HomeSlideKind, ServiceHighlight, TeamMember, Testimonial


class HomeView(TemplateView):
    template_name = "frontoffice/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        slides = HomeSlide.objects.filter(is_active=True).order_by("order", "id")
        context.update(
            {
                "home_slides": slides.filter(kind=HomeSlideKind.HERO),
                "benefits_slides": slides.filter(kind=HomeSlideKind.BENEFIT),
                "service_highlights": ServiceHighlight.objects.filter(is_active=True).order_by(
                    "order", "id"
                ),
                "testimonials": Testimonial.objects.filter(is_active=True).order_by("order", "id"),
            }
        )
        return context


class AboutUsView(TemplateView):
    template_name = "frontoffice/about_us.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["team_members"] = TeamMember.objects.filter(is_active=True).order_by("order", "id")
        return context


class PricingView(TemplateView):
    template_name = "frontoffice/pricing.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        available_offers = (
            CatalogOffer.objects.filter(
                is_active=True,
                is_public=True,
                catalog_item__is_active=True,
            )
            .filter(
                Q(catalog_item__origin="website")
                | Q(
                    catalog_item__remote_deleted_at__isnull=True,
                    catalog_item__toconline_id__isnull=False,
                )
            )
            .select_related("catalog_item", "level")
            .order_by("level__order", "order", "name")
        )
        catalog_types = CatalogType.objects.filter(is_active=True).prefetch_related(
            Prefetch("offers", queryset=available_offers, to_attr="public_offers")
        )

        families = []
        for catalog_type in catalog_types:
            if not catalog_type.public_offers:
                continue
            if catalog_type.workflow == CatalogWorkflow.DATE_RANGE:
                action_label = _("Book")
            elif catalog_type.is_scheduled:
                action_label = _("See plans")
            else:
                action_label = _("See services")
            families.append(
                {
                    "id": catalog_type.slug,
                    "name": catalog_type.name,
                    "services": catalog_type.public_offers,
                    "action_url": catalog_type.public_url,
                    "action_label": action_label,
                }
            )

        context["families"] = families

        return context


class ContactUsView(FormView):
    form_class = forms.ContactUsForm
    template_name = "frontoffice/contact_us.html"
    success_url = reverse_lazy("frontoffice:contact_us")

    def form_valid(self, form):
        form.save()
        messages.success(self.request, _("Thank you! Your message has been sent."))
        return super().form_valid(form)


class LegalDocumentDetailView(TemplateView):
    doc_type: LegalDocument.DocumentType

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)

        where = {
            "doc_type": self.doc_type,
            "effective_date__lte": now().today(),
        }

        context["object"] = LegalDocument.objects_active.filter(**where).first()

        return context


class TermsAndConditionsView(LegalDocumentDetailView):
    template_name = "frontoffice/terms_and_conditions.html"
    doc_type = LegalDocument.DocumentType.TERMS


class PrivacyPolicyView(LegalDocumentDetailView):
    template_name = "frontoffice/privacy_policy.html"
    doc_type = LegalDocument.DocumentType.PRIVACY
