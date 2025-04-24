from typing import Any
from django.urls import reverse_lazy
from django.views.generic import TemplateView, FormView
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from django.utils.timezone import now

from backoffice.models import LegalDocument
from olhocao.deepl import trans
from olhocao.toconline import toconline, TocOnlineResource


from . import forms


class HomeView(TemplateView):
    template_name = "frontoffice/home.html"

    extra_context = {
        "home_slides": [
            {
                "src": "https://picsum.photos/2880/1800?r=1",
                "alt": _("OlhóCão having fun!"),
                "title": _("OlhóCão having fun!"),
                "description": _("OlhóCão is a wild park where your dog goes full zoomies, makes furry friends, and forgets taxes exist. It's a doggy dreamland for fun and freedom."),
                "cta_link": reverse_lazy('frontoffice:pricing'),
                "cta_text": _("Discover Our Services"),
            },
            {
                "src": "https://picsum.photos/2880/1800?r=2",
                "alt": _("LavóCão so he smells nice!"),
                "title": _("LavóCão so he smells nice!"),
                "description": _("We wash, fluff, and de-funk your furry beast. LavóCão turns your stink monster into a soft, snuggly cloud that’s finally welcome on the couch again."),
                "cta_link": reverse_lazy('frontoffice:pricing'),
                "cta_text": _("Discover Our Services"),
            },
            {
                "src": "https://picsum.photos/2880/1800?r=3",
                "alt": _("TreinóCão to be a good boy!"),
                "title": _("TreinóCão to be a good boy!"),
                "description": _("From couch destroyer to polite pupper—TreinóCão teaches obedience, social skills, and how *not* to embarrass you at the park."),
                "cta_link": reverse_lazy('frontoffice:pricing'),
                "cta_text": _("Discover Our Services"),
            },
            {
                "src": "https://picsum.photos/2880/1800?r=4",
                "alt": _("DeixóCão: we take care while you take off!"),
                "title": _("DeixóCão: we take care while you take off!"),
                "description": _("Vacation time? DeixóCão is your dog's home away from home—with playtime, cuddles, and way too many treats. You relax, we handle the tail-wagging."),
                "cta_link": reverse_lazy('frontoffice:pricing'),
                "cta_text": _("Discover Our Services"),
            },
        ],
        "benefits_slides": [
            {
                "src": "https://picsum.photos/2880/1800?r=4",
                "alt": _('Trust in the best training'),
                "title": _('Trust in the best training'),
                "description": _("Our pros have years of experience and Jedi-level dog-whispering skills. Your furry tornado is in safe hands."),
            },
            {
                "src": "https://picsum.photos/2880/1800?r=5",
                "alt": _("Fun for everyone!"),
                "title": _("Fun for everyone!"),
                "description": _("It’s basically a theme park for dogs. Walks, jumps, races, tail chases, group zoomies—we've got it all. Humans might get jealous."),
            },
            {
                "src": "https://picsum.photos/2880/1800?r=6",
                "alt": _("Socialization and cognitive development"),
                "title": _("Socialization and cognitive development"),
                "description": _("Shy pups? Overly excited bark machines? We help them mingle, grow brains, and maybe even learn not to eat your socks."),
            },
            {
                "src": "https://picsum.photos/2880/1800?r=7",
                "alt": _("More comfort for the whole family"),
                "title": _("More comfort for the whole family"),
                "description": _("No time? No car? No problem. We pick up and drop off your pup, all clean and happy. It’s like Uber, but fluffier."),
            },
            {
                "src": "https://picsum.photos/2880/1800?r=8",
                "alt": _("Tailored for your dog’s needs"),
                "title": _("Tailored for your dog’s needs"),
                "description": _("Big or small, shy or hyper—we customize the fun. Your dog’s happiness program is designed like a 5-star dogcation."),
            },
        ],
    }


class AboutUsView(TemplateView):
    template_name = "frontoffice/about_us.html"


class PricingView(TemplateView):
    template_name = "frontoffice/pricing.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        toc_families = toconline.list(TocOnlineResource.ITEM_FAMILY)
        context["families"] = []

        for toc_family in toc_families:
            toc_family_services = toconline.list(
                TocOnlineResource.SERVICES,
                item_families=toc_family["id"],
                is_active=True,
            )

            family_services = list(
                map(
                    lambda svc: {
                        "id": svc["id"],
                        "name": svc["attributes"]["item_description"],
                        "price": svc["attributes"]["sales_price"],
                    },
                    toc_family_services,
                )
            )
            family = {
                "id": toc_family["id"],
                "name": toc_family["attributes"]["name"],
                "services": family_services,
            }

            context["families"].append(family)

        return context


class ContactUsView(FormView):
    form_class = forms.ContactUsForm
    template_name = 'frontoffice/contact_us.html'
    success_url = reverse_lazy('frontoffice:contact_us')

    def form_valid(self, form):
        form.save()
        messages.success(self.request, _("Thank you! Your message has been sent."))
        return super().form_valid(form)


class LegalDocumentDetailView(TemplateView):
    doc_type: LegalDocument.DocumentType

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)

        where = {
            'doc_type': self.doc_type,
            'effective_date__lte': now().today(),
        }

        context["object"] = LegalDocument.objects_active.filter(**where).first()

        return context


class TermsAndConditionsView(LegalDocumentDetailView):
    template_name = 'frontoffice/terms_and_conditions.html'
    doc_type = LegalDocument.DocumentType.TERMS


class PrivacyPolicyView(LegalDocumentDetailView):
    template_name = 'frontoffice/privacy_policy.html'
    doc_type = LegalDocument.DocumentType.PRIVACY
