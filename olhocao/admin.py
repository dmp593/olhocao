from django.contrib.admin import AdminSite


class DeveloperAdminSite(AdminSite):
    """Keep Django's technical administration separate from staff operations."""

    site_header = "Olhó Cão · Administração técnica"
    site_title = "Olhó Cão"
    index_title = "Administração técnica"

    def has_permission(self, request):
        return request.user.is_active and request.user.is_superuser
