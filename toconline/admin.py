from django.contrib import admin


from .models import TocOnlineToken


@admin.register(TocOnlineToken)
class TocOnlineTokenAdmin(admin.ModelAdmin):
    list_display = ("token_type", "acquired_at", "expires_in")
