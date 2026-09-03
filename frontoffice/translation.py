from modeltranslation.translator import TranslationOptions, translator

from .models import (
    ContactSubject,
    HomeSlide,
    Partner,
    ServiceHighlight,
    SiteSettings,
    TeamMember,
    Testimonial,
)


class ContactSubjectTranslationOptions(TranslationOptions):
    fields = ("name",)


translator.register(ContactSubject, ContactSubjectTranslationOptions)


class SiteSettingsTranslationOptions(TranslationOptions):
    fields = (
        "weekday_hours",
        "weekend_hours",
        "home_services_eyebrow",
        "home_services_title",
        "home_services_subtitle",
        "home_testimonials_eyebrow",
        "home_testimonials_title",
        "home_testimonials_subtitle",
        "about_eyebrow",
        "about_title",
        "about_subtitle",
        "about_history_title",
        "about_history",
        "facility_title",
        "facility_description",
        "team_title",
        "team_subtitle",
        "contact_eyebrow",
        "contact_title",
        "contact_subtitle",
        "contact_visit_notice",
        "pricing_eyebrow",
        "pricing_title",
        "pricing_subtitle",
    )


class HomeSlideTranslationOptions(TranslationOptions):
    fields = ("title", "description", "cta_text", "image_alt")


class ServiceHighlightTranslationOptions(TranslationOptions):
    fields = ("title", "description", "features", "action_text")


class TestimonialTranslationOptions(TranslationOptions):
    fields = ("headline", "body", "attribution", "image_alt")


class TeamMemberTranslationOptions(TranslationOptions):
    fields = ("role", "description", "specialization", "image_alt")


class PartnerTranslationOptions(TranslationOptions):
    fields = ("category", "tagline")


translator.register(SiteSettings, SiteSettingsTranslationOptions)
translator.register(HomeSlide, HomeSlideTranslationOptions)
translator.register(ServiceHighlight, ServiceHighlightTranslationOptions)
translator.register(Testimonial, TestimonialTranslationOptions)
translator.register(TeamMember, TeamMemberTranslationOptions)
translator.register(Partner, PartnerTranslationOptions)
