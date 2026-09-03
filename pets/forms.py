from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Pet, PetSpecies


class PetForm(forms.ModelForm):
    class Meta:
        model = Pet
        fields = [
            "name",
            "breed",
            "gender",
            "birthdate",
            "microship_number",
            "height_cm",
            "weight_kg",
            "photo",
            "is_sterilized",
            "is_vaccinated",
            "medication",
            "allergies",
            "notes",
        ]
        widgets = {
            "birthdate": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "medication": forms.Textarea(attrs={"rows": 3}),
            "allergies": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["gender"].choices = (
            ("", _("Choose a gender")),
            *Pet._meta.get_field("gender").choices,
        )

    def save(self, commit=True):
        if not self.instance.species_id:
            self.instance.species = PetSpecies.get_dog_species()
        return super().save(commit=commit)


class PetAdminForm(PetForm):
    class Meta(PetForm.Meta):
        fields = ["owner"] + PetForm.Meta.fields

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owner"].empty_label = _("Choose a customer")
