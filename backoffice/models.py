from django.db import models
from django.utils.translation import gettext_lazy as _

from olhocao import managers


class LegalDocument(models.Model):
    class DocumentType(models.TextChoices):
        TERMS = 'terms', _('Terms and Conditions')
        PRIVACY = 'privacy', _('Privacy Policy')

    doc_type = models.CharField(
        max_length=20,
        choices=DocumentType.choices,
        verbose_name=_('Document Type'),
        help_text=_('Select whether this is a Terms and Conditions or Privacy Policy document.')
    )

    name = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        verbose_name=_('Name'),
        help_text=_('Name of the legal document shown to users.')
    )

    heading = models.TextField(
        null=True,
        blank=True,
        verbose_name=_('Heading'),
        help_text=_('Heading of the legal document.')
    )

    effective_date = models.DateField(
        verbose_name=_('Effective Date'),
        help_text=_('Date from which this document is considered effective.')
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_('Last Updated At'),
        help_text=_('Automatically updated whenever the document is changed.')
    )

    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        default=None,
        verbose_name=_('Deleted At'),
        help_text=_('Marks this document as deleted/deprecated.')
    )

    objects = managers.Manager()
    objects_active = managers.Manager(deleted_at__isnull=True)

    def __str__(self):
        if self.name:
            f'{self.name} ({self.effective_date})'

        return f'{self.get_doc_type_display()} ({self.effective_date})'

    class Meta:
        verbose_name = _('Legal Document')
        verbose_name_plural = _('Legal Documents')
        ordering = ['-effective_date', '-updated_at']


class LegalDocumentSection(models.Model):
    document = models.ForeignKey(
        LegalDocument,
        related_name='sections',
        related_query_name='section',
        on_delete=models.CASCADE,
        verbose_name=_('Document'),
        help_text=_('The legal document this section belongs to.')
    )

    title = models.CharField(
        max_length=255,
        verbose_name=_('Section Title'),
        help_text=_('Title of the section (e.g., Reservations, Data Collection, etc.).')
    )
    
    order = models.PositiveIntegerField(
        default=0,
        verbose_name=_('Order'),
        help_text=_('Defines the display order of sections within the document.')
    )

    class Meta:
        ordering = ['order']
        verbose_name = _('Legal Section')
        verbose_name_plural = _('Legal Sections')


class LegalDocumentSectionLineItem(models.Model):
    section = models.ForeignKey(
        LegalDocumentSection,
        related_name='items',
        related_query_name='item',
        on_delete=models.CASCADE,
        verbose_name=_('Section'),
        help_text=_('The section this line item belongs to.')
    )

    text = models.TextField(
        verbose_name=_('Text'),
        help_text=_('The actual content of this line item (e.g., a clause, condition, or explanation).')
    )

    order = models.PositiveIntegerField(
        default=0,
        verbose_name=_('Order'),
        help_text=_('Defines the display order of line items within the section.')
    )

    class Meta:
        ordering = ['order']
        verbose_name = _('Line Item')
        verbose_name_plural = _('Line Items')

    def __str__(self):
        return self.text[:60] + ('…' if len(self.text) > 60 else '')
