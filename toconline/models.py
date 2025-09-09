from datetime import datetime

from django.utils.timezone import timedelta, now
from django.db import models


class TocOnlineToken(models.Model):
    access_token = models.CharField(max_length=255)
    refresh_token = models.CharField(max_length=255)

    acquired_at = models.DateTimeField(auto_now_add=True)
    refreshed_at = models.DateTimeField(auto_now=True)

    expires_in = models.IntegerField()
    token_type = models.CharField(max_length=50)

    @property
    def expires_at(self) -> datetime:
        if self.refreshed_at:
            return self.refreshed_at + timedelta(seconds=self.expires_in)

        return self.acquired_at + timedelta(seconds=self.expires_in)

    @property
    def is_expired(self) -> bool:
        datetime_now = now()
        skew = timedelta(seconds=30)

        # If the token expires within the next `skew` seconds, consider it
        # expired (proactive refresh to avoid using near-expiry tokens).
        return self.expires_at <= datetime_now + skew

    class Meta:
        ordering = [
            '-refreshed_at',
            '-acquired_at'
        ]
