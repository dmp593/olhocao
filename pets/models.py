import uuid
import pathlib
import mimetypes

from datetime import date

from django.db import models

from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _


from accounts.models import Account


def pet_photo_upload_to(instance, filename):
    extension = mimetypes.guess_extension(filename)

    if not extension:
        extension = pathlib.Path(filename).suffix

    return f"pets/{uuid.uuid4().hex}{extension}"


class PetSpecies(models.Model):
    name = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        verbose_name=_('name'),
        help_text=_('The species name of the pet'),
    )

    is_active = models.BooleanField(
        default=True,
        verbose_name=_('is active'),
        help_text=_('Whether the pet species is active in the system or not'),
    )

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _('Pet Species')
        verbose_name_plural = _('Pet Species')


class Pet(models.Model):
    species = models.ForeignKey(
        PetSpecies,
        on_delete=models.PROTECT,
        related_name='pets',
        related_query_name='pet',
        verbose_name=_('species'),
        help_text=_('The species of the pet'),
    )

    breed = models.CharField(
        max_length=100,
        default="Mixed Breed",
        verbose_name=_('breed'),
        help_text=_('The breed of the pet'),
    )

    gender = models.CharField(
        max_length=10,
        choices=(
            ('M', _('male')),
            ('F', _('female'))
        ),
        verbose_name=_('gender'),
        help_text=_('The gender of the pet'),
    )

    name = models.CharField(
        max_length=100,
        verbose_name=_('name'),
        help_text=_('The name of the pet'),
    )

    birthdate = models.DateField(
        verbose_name=_('birthdate'),
        help_text=_('The birthdate of the pet'),
    )

    weight_kg = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_('weight (kg)'),
        help_text=_('The weight of the pet in kilograms'),
    )

    height_cm = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_('weight (cm)'),
        help_text=_('The weight of the pet in centimeters'),
    )

    microship_number = models.CharField(
        max_length=15,
        blank=True,
        verbose_name=_('microchip number'),
        help_text=_('The microchip number of the pet'),
    )

    photo = models.ImageField(
        upload_to=pet_photo_upload_to,
        blank=True,
        verbose_name=_('photo'),
        help_text=_('The photo of the pet'),
    )

    is_sterilized = models.BooleanField(
        default=False,
        verbose_name=_('is sterilized'),
        help_text=_('Whether the pet is sterilized or not'),
    )

    is_vaccinated = models.BooleanField(
        default=False,
        verbose_name=_('is vaccinated'),
        help_text=_('Whether the pet is vaccinated or not'),
    )

    is_allergic = models.BooleanField(
        default=False,
        verbose_name=_('is allergic'),
        help_text=_('Whether the pet is allergic or not'),
    )

    is_medicated = models.BooleanField(
        default=False,
        verbose_name=_('is medicated'),
        help_text=_('Whether the pet is medicated or not'),
    )

    medication = models.TextField(
        blank=True,
        verbose_name=_('medication'),
        help_text=_('Medication of the pet'),
    )

    allergies = models.TextField(
        blank=True,
        verbose_name=_('allergies'),
        help_text=_('Allergies of the pet'),
    )

    notes = models.TextField(
        blank=True,
        verbose_name=_('notes'),
        help_text=_('Additional notes about the pet'),
    )

    owner = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name='pets',
        related_query_name='pet',
        verbose_name=_('owner'),
        help_text=_('The owner of the pet'),
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_('created at'),
        help_text=_('The date and time when the pet was created'),
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_('updated at'),
        help_text=_('The date and time when the pet was last updated'),
    )

    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('deleted at'),
        help_text=_('The date and time when the pet was deleted'),
    )

    @property
    def age(self):
        today = date.today()
        born = self.birthdate
        delta = today - born
        days = delta.days
        
        if days < 60:  # Less than 2 months
            weeks = days // 7
            return _("%d weeks old") % weeks
        elif days < 365:  # Less than 1 year
            months = today.month - born.month
            if today.day < born.day:
                months -= 1
            if months < 0:
                months += 12
            return _("%d months old") % months
        else:
            years = today.year - born.year
            if (today.month, today.day) < (born.month, born.day):
                years -= 1
            return _("%d years old") % years

    def __str__(self):
        return self.name
