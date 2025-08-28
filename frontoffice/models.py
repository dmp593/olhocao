from django.db import models
from django.utils.translation import gettext_lazy as _


class ContactSubject(models.Model):
    name = models.CharField(
        max_length=100,
        verbose_name=_('Name'),
        help_text=_('Contact Subject Name')
    )

    order = models.IntegerField(
        default=999,
        verbose_name=_('Order'),
        help_text=_('Contact Subject Order')
    )

    def __str__(self):
        return self.name


class ContactRequest(models.Model):
    subject = models.ForeignKey(
        to=ContactSubject,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_('Subject'),
        help_text=_('Contact Subject')
    )

    name = models.CharField(
        max_length=128,
        verbose_name=_('Name'),
        help_text=_('Your Name'),
    )

    phone = models.CharField(
        max_length=32,
        verbose_name=_('Phone'),
        help_text=_('Your Phone')
    )

    email = models.EmailField(
        verbose_name=_('Email'),
        help_text=_('Your Email Account')
    )

    message = models.TextField(
        verbose_name=_('Message'),
        help_text=_('Your Message')
    )

    created_at = models.DateTimeField(
        null=True,
        blank=True,
        auto_now_add=True,
        verbose_name=_('Created At'),
        help_text=_('When the contact request was created')
    )

    read_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('Read At'),
        help_text=_('When the contact request was read')
    )

    class Meta:
        ordering = [
            '-created_at'
        ]
