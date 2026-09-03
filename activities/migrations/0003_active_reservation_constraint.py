from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("activities", "0002_activity_reservation_event"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="activityreservation",
            name="one_active_activity_reservation_per_pet",
        ),
        migrations.AddConstraint(
            model_name="activityreservation",
            constraint=models.UniqueConstraint(
                condition=models.Q(status__in=("booked", "attended")),
                fields=("session", "pet"),
                name="one_active_activity_reservation_per_pet",
            ),
        ),
    ]
