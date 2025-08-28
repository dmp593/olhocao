from django.db import models
from django.utils.functional import cached_property

from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from olhocao.toconline import get_toconline, TocOnlineResource


User = get_user_model()


class Account(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='account',
        related_query_name='account',
        verbose_name=_('user'),
        help_text=_('The user that this account belongs to'),
    )

    toconline_customer_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        unique=True,
        db_index=True,
        verbose_name=_('Customer ID (toconline)'),
        help_text=_(
            'The ID of the customer in the toconline platform '
            '(invoice system)'
        ),
    )

    @property
    def has_toconline_customer(self):
        return bool(self.toconline_customer_id)

    @cached_property
    def toconline_customer(self):
        if not self.has_toconline_customer:
            return None

        return get_toconline().get(
            TocOnlineResource.CUSTOMERS,
            self.toconline_customer_id
        )

    @property
    def phone_number(self):
        """
        Best-effort phone for this account pulled from the linked
        ToCOnline customer. Falls back to None when unavailable.
        """
        customer = self.toconline_customer

        if not customer:
            return None

        attrs = customer.get('attributes', {})

        return (
            attrs.get('mobile_number', None)
            or attrs.get('phone_number', None)
        )

    def __str__(self):
        return f'{self.user.username} | {self.user.email}'
