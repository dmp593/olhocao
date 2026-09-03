from django.contrib.admin.apps import AdminConfig


class DeveloperAdminConfig(AdminConfig):
    default_site = "olhocao.admin.DeveloperAdminSite"
