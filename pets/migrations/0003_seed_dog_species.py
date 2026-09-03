from django.db import migrations
from django.db.models import Q


def seed_dog_species(apps, schema_editor):
    pet_species = apps.get_model("pets", "PetSpecies")
    dog = pet_species.objects.filter(
        Q(name__iexact="Dog") | Q(name_en__iexact="Dog") | Q(name_pt__iexact="Cão")
    ).first()
    if dog is None:
        pet_species.objects.create(
            name="Dog",
            name_en="Dog",
            name_pt="Cão",
            is_active=True,
        )
    elif not dog.is_active:
        dog.is_active = True
        dog.save(update_fields=("is_active",))


class Migration(migrations.Migration):
    dependencies = [("pets", "0002_remove_pet_is_allergic_remove_pet_is_medicated_and_more")]

    operations = [migrations.RunPython(seed_dog_species, migrations.RunPython.noop)]
