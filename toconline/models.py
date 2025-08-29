from datetime import datetime

from django.utils.timezone import timedelta, now
from django.db import models


class TocOnlineToken(models.Model):
    access_token = models.CharField(max_length=255)
    refresh_token = models.CharField(max_length=255)
    acquired_at = models.DateTimeField(auto_now_add=True)
    expires_in = models.IntegerField()
    token_type = models.CharField(max_length=50)

    @property
    def expires_at(self) -> datetime:
        return self.acquired_at + timedelta(seconds=self.expires_in)

    @property
    def is_expired(self) -> bool:
        return self.expires_at < now()
