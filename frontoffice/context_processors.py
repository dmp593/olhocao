from .models import Partner, SiteSettings, SocialLink


def public_site_content(request):
    """Expose the small shared public-content set used by the footer and pages."""

    return {
        "site_settings": SiteSettings.current(),
        "site_partners": Partner.objects.filter(is_active=True).order_by("order", "id"),
        "site_social_links": SocialLink.objects.filter(is_active=True).order_by("order", "id"),
    }
