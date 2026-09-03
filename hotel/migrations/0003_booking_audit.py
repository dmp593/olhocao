import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("hotel", "0002_rename_booking_account_customer"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="booking",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="created_bookings",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name="BookingEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_type", models.CharField(choices=[("created", "Created"), ("rescheduled", "Rescheduled"), ("cancelled", "Cancelled"), ("payment_confirmed", "Payment confirmed"), ("refund_requested", "Refund requested"), ("document_issued", "Document issued")], max_length=40)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="booking_events", to=settings.AUTH_USER_MODEL)),
                ("booking", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="hotel.booking")),
            ],
            options={
                "ordering": ("-created_at", "-id"),
                "indexes": [models.Index(fields=["booking", "event_type"], name="booking_event_type_idx")],
            },
        ),
    ]
