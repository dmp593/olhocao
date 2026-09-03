from datetime import date

from django.test import TestCase
from django.urls import reverse

from backoffice.models import (
    LegalDocument,
    LegalDocumentSection,
    LegalDocumentSectionLineItem,
)
from catalog.models import CatalogType, CatalogWorkflow
from hotel.tests.factories import create_catalog_item, create_catalog_offer

from .models import ContactSubject, HomeSlide, HomeSlideKind, ServiceHighlight, SiteSettings


class PricingVisibilityTests(TestCase):
    def test_only_explicitly_public_catalog_items_are_listed(self):
        visible = create_catalog_item(remote_id="visible", name="Hotel canino")
        visible.is_public = True
        visible.save(update_fields=("is_public",))
        create_catalog_offer(visible)
        hidden = create_catalog_item(remote_id="hidden", name="Internal test item")
        hidden_offer = create_catalog_offer(hidden)
        hidden_offer.is_public = False
        hidden_offer.save(update_fields=("is_public", "updated_at"))

        response = self.client.get(reverse("frontoffice:pricing"))

        self.assertContains(response, "Hotel canino")
        self.assertNotContains(response, "Internal test item")


class PublicPageConsistencyTests(TestCase):
    def test_browser_favicon_request_redirects_to_the_brand_icon(self):
        response = self.client.get(reverse("favicon"))

        self.assertRedirects(
            response,
            "/static/paws.svg",
            status_code=301,
            fetch_redirect_response=False,
        )

    def test_contact_page_uses_real_contact_information_without_demo_amenities(self):
        response = self.client.get(reverse("frontoffice:contact_us"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "olhocao@olhocao.pt")
        self.assertContains(response, 'name="message"')
        self.assertContains(response, "Choose a subject")
        self.assertNotContains(response, "Squirrel surveillance team")
        self.assertNotContains(response, "karaoke")

    def test_legal_pages_have_a_useful_empty_state(self):
        terms = self.client.get(reverse("frontoffice:terms_and_conditions"))
        privacy = self.client.get(reverse("frontoffice:privacy_policy"))

        self.assertContains(terms, "No Terms Available")
        self.assertContains(privacy, "No Privacy Policy Available")

    def test_active_legal_document_renders_its_sections(self):
        document = LegalDocument.objects.create(
            doc_type=LegalDocument.DocumentType.TERMS,
            name="Service terms",
            effective_date=date.today(),
        )
        section = LegalDocumentSection.objects.create(
            document=document,
            title="Booking rules",
        )
        LegalDocumentSectionLineItem.objects.create(
            section=section,
            text="Bookings require confirmation.",
        )

        response = self.client.get(reverse("frontoffice:terms_and_conditions"))

        self.assertContains(response, "Service terms")
        self.assertContains(response, "Booking rules")
        self.assertContains(response, "Bookings require confirmation.")

    def test_public_pages_do_not_use_permanent_animation_loops(self):
        for route_name in (
            "frontoffice:home",
            "frontoffice:about_us",
            "frontoffice:contact_us",
            "frontoffice:terms_and_conditions",
            "frontoffice:privacy_policy",
        ):
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(route_name))
                self.assertNotContains(response, "animate__infinite")


class ManagedPublicContentTests(TestCase):
    def test_service_highlight_follows_renamed_dynamic_catalog_type(self):
        catalog_type = CatalogType.objects.create(
            name="ATL",
            slug="atl",
            workflow=CatalogWorkflow.FULL_DAY_SLOT,
            default_capacity=12,
        )
        highlight = ServiceHighlight.objects.create(
            icon=ServiceHighlight.Icon.DAYCARE,
            title="Dog daycare",
            description="Supervised days",
            features="Safe play",
            action_text="See plans",
            catalog_type=catalog_type,
        )

        catalog_type.name = "Clube diário"
        catalog_type.slug = "clube-diario"
        catalog_type.save(update_fields=("name", "slug", "updated_at"))
        highlight.refresh_from_db()

        self.assertEqual(highlight.destination_url, "/activities/?type=clube-diario")

    def test_seeded_public_content_renders_in_both_supported_languages(self):
        english = self.client.get(reverse("frontoffice:home"), HTTP_ACCEPT_LANGUAGE="en")
        portuguese = self.client.get(reverse("frontoffice:home"), HTTP_ACCEPT_LANGUAGE="pt")

        self.assertContains(english, "Care for each part of your dog&#x27;s routine")
        self.assertContains(portuguese, "Cuidados para cada parte da rotina do seu cão")
        self.assertContains(english, "Dog hotel")
        self.assertContains(portuguese, "Hotel canino")

    def test_staff_managed_text_is_escaped_on_public_pages(self):
        slide = HomeSlide.objects.filter(kind=HomeSlideKind.HERO).first()
        slide.title_en = '<script data-test="stored-xss">alert(1)</script>'
        slide.save(update_fields=("title_en",))

        response = self.client.get(reverse("frontoffice:home"), HTTP_ACCEPT_LANGUAGE="en")

        self.assertNotContains(response, '<script data-test="stored-xss">', html=False)
        self.assertContains(
            response,
            "&lt;script data-test=&quot;stored-xss&quot;&gt;alert(1)&lt;/script&gt;",
            html=False,
        )

    def test_inactive_contact_subject_is_not_offered(self):
        ContactSubject.objects.create(
            name="Hidden subject",
            name_en="Hidden subject",
            name_pt="Assunto escondido",
            is_active=False,
        )

        response = self.client.get(reverse("frontoffice:contact_us"))

        self.assertNotContains(response, "Hidden subject")

    def test_shared_settings_are_reflected_without_template_changes(self):
        site_settings = SiteSettings.current()
        site_settings.public_email = "equipa@example.com"
        site_settings.pricing_title_en = "Current service prices"
        site_settings.pricing_title_pt = "Preços atuais dos serviços"
        site_settings.save(
            update_fields=("public_email", "pricing_title_en", "pricing_title_pt", "updated_at")
        )

        pricing = self.client.get(reverse("frontoffice:pricing"), HTTP_ACCEPT_LANGUAGE="pt")

        self.assertContains(pricing, "Preços atuais dos serviços")
        self.assertContains(pricing, "equipa@example.com")
