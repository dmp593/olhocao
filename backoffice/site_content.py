from dataclasses import dataclass

from django.db import models
from django.forms import ModelForm
from django.utils.translation import gettext_lazy as _

from frontoffice.models import (
    ContactSubject,
    HomeSlide,
    Partner,
    ServiceHighlight,
    SocialLink,
    TeamMember,
    Testimonial,
)

from .forms import (
    ContactSubjectForm,
    HomeSlideForm,
    PartnerForm,
    ServiceHighlightForm,
    SocialLinkForm,
    TeamMemberForm,
    TestimonialForm,
)

SITE_SETTINGS_FIELDSETS = (
    (
        _("Business details"),
        _("Shared contact, address and map information."),
        (
            "company_name",
            "public_email",
            "primary_phone",
            "secondary_phone",
            "address_line_1",
            "address_line_2",
            "maps_embed_url",
            "directions_url",
        ),
    ),
    (
        _("Opening hours"),
        _("Write the customer-facing schedule in both languages."),
        ("weekday_hours_pt", "weekday_hours_en", "weekend_hours_pt", "weekend_hours_en"),
    ),
    (
        _("Homepage services"),
        _("Heading above the service cards."),
        (
            "home_services_eyebrow_pt",
            "home_services_eyebrow_en",
            "home_services_title_pt",
            "home_services_title_en",
            "home_services_subtitle_pt",
            "home_services_subtitle_en",
        ),
    ),
    (
        _("Homepage testimonials"),
        _("Heading above testimonials and care principles."),
        (
            "home_testimonials_eyebrow_pt",
            "home_testimonials_eyebrow_en",
            "home_testimonials_title_pt",
            "home_testimonials_title_en",
            "home_testimonials_subtitle_pt",
            "home_testimonials_subtitle_en",
        ),
    ),
    (
        _("About page"),
        _("Story, facility and team headings."),
        (
            "about_eyebrow_pt",
            "about_eyebrow_en",
            "about_title_pt",
            "about_title_en",
            "about_subtitle_pt",
            "about_subtitle_en",
            "about_history_title_pt",
            "about_history_title_en",
            "about_history_pt",
            "about_history_en",
            "facility_title_pt",
            "facility_title_en",
            "facility_description_pt",
            "facility_description_en",
            "team_title_pt",
            "team_title_en",
            "team_subtitle_pt",
            "team_subtitle_en",
        ),
    ),
    (
        _("Contact page"),
        _("Introduction and appointment notice."),
        (
            "contact_eyebrow_pt",
            "contact_eyebrow_en",
            "contact_title_pt",
            "contact_title_en",
            "contact_subtitle_pt",
            "contact_subtitle_en",
            "contact_visit_notice_pt",
            "contact_visit_notice_en",
        ),
    ),
    (
        _("Pricing page"),
        _("Introduction above the live catalog."),
        (
            "pricing_eyebrow_pt",
            "pricing_eyebrow_en",
            "pricing_title_pt",
            "pricing_title_en",
            "pricing_subtitle_pt",
            "pricing_subtitle_en",
        ),
    ),
)


@dataclass(frozen=True)
class ContentCollection:
    slug: str
    model: type[models.Model]
    form_class: type[ModelForm]
    title: object
    singular: object
    description: object


CONTENT_COLLECTIONS = (
    ContentCollection(
        slug="slides",
        model=HomeSlide,
        form_class=HomeSlideForm,
        title=_("Carousels"),
        singular=_("carousel slide"),
        description=_("Manage the main and benefits carousel images, text and actions."),
    ),
    ContentCollection(
        slug="services",
        model=ServiceHighlight,
        form_class=ServiceHighlightForm,
        title=_("Homepage services"),
        singular=_("service card"),
        description=_("Choose the service cards shown on the homepage."),
    ),
    ContentCollection(
        slug="testimonials",
        model=Testimonial,
        form_class=TestimonialForm,
        title=_("Testimonials and care principles"),
        singular=_("testimonial or care principle"),
        description=_("Publish real testimonials or the principles that guide the team."),
    ),
    ContentCollection(
        slug="team",
        model=TeamMember,
        form_class=TeamMemberForm,
        title=_("Team"),
        singular=_("team member"),
        description=_("Keep the public team presentation and photographs current."),
    ),
    ContentCollection(
        slug="partners",
        model=Partner,
        form_class=PartnerForm,
        title=_("Partners"),
        singular=_("partner"),
        description=_("Manage the trusted organizations shown in the footer."),
    ),
    ContentCollection(
        slug="social-links",
        model=SocialLink,
        form_class=SocialLinkForm,
        title=_("Social links"),
        singular=_("social link"),
        description=_("Manage the official social profiles linked from the website."),
    ),
    ContentCollection(
        slug="contact-subjects",
        model=ContactSubject,
        form_class=ContactSubjectForm,
        title=_("Contact subjects"),
        singular=_("contact subject"),
        description=_("Control the translated reasons available in the contact form."),
    ),
)

CONTENT_COLLECTIONS_BY_SLUG = {collection.slug: collection for collection in CONTENT_COLLECTIONS}


def get_content_collection(slug):
    return CONTENT_COLLECTIONS_BY_SLUG.get(slug)
