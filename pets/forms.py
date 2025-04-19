from django import forms

from .models import Pet


class PetForm(forms.ModelForm):
    class Meta:
        model = Pet
        fields = [
            'name', 'species', 'breed', 'gender', 'birthdate',
            'microship_number', 'height_cm', 'weight_kg', 'photo', 
            'is_sterilized', 'is_vaccinated', 'is_allergic', 'is_medicated',
            'medication', 'allergies', 'notes'
        ]
        widgets = {
            'birthdate': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
            'medication': forms.Textarea(attrs={'rows': 3}),
            'allergies': forms.Textarea(attrs={'rows': 3}),
        }
