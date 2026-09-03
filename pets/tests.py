from datetime import date

from django.test import TestCase
from django.urls import reverse

from hotel.tests.factories import create_account
from pets.models import Pet, PetSpecies


class DogFormTests(TestCase):
    def setUp(self):
        self.customer = create_account()
        self.client.force_login(self.customer.user)

    def test_form_does_not_require_species_configuration(self):
        response = self.client.get(reverse("pets:create"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="species"')
        self.assertContains(response, "species is assigned automatically")
        self.assertContains(response, "Choose a gender")
        self.assertNotContains(response, "- Select an option -")

    def test_creating_a_dog_assigns_seeded_dog_species(self):
        response = self.client.post(
            reverse("pets:create"),
            {
                "name": "Suki",
                "breed": "Mixed Breed",
                "gender": "F",
                "birthdate": date(2020, 1, 2),
                "microship_number": "",
                "height_cm": "",
                "weight_kg": "",
                "is_sterilized": True,
                "is_vaccinated": True,
                "medication": "",
                "allergies": "",
                "notes": "",
            },
        )

        self.assertRedirects(response, reverse("pets:list"))
        dog = Pet.objects.get(name="Suki")
        self.assertEqual(dog.species.name_en, "Dog")
        self.assertTrue(dog.species.is_active)

    def test_missing_dog_species_is_recreated_safely(self):
        PetSpecies.objects.all().delete()

        self.client.post(
            reverse("pets:create"),
            {
                "name": "Luna",
                "breed": "Labrador",
                "gender": "F",
                "birthdate": date(2021, 2, 3),
            },
        )

        self.assertEqual(Pet.objects.get(name="Luna").species.name_en, "Dog")
