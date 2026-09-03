from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("activities", "0016_entitlementrefund"),
    ]

    operations = [
        migrations.AddField(
            model_name="serviceentitlement",
            name="source_activity_reservation",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Activity reservation where this optional service was selected. "
                    "The purchase remains valid even if that session is later changed."
                ),
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="add_on_entitlements",
                to="activities.activityreservation",
            ),
        ),
        migrations.AddConstraint(
            model_name="serviceentitlement",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("source_activity_reservation__isnull", False),
                    (
                        "status__in",
                        (
                            "pending",
                            "active",
                            "past_due",
                            "cancel_at_period_end",
                            "suspended",
                        ),
                    ),
                ),
                fields=("source_activity_reservation", "offering"),
                name="one_open_add_on_per_activity_reservation",
            ),
        ),
    ]
