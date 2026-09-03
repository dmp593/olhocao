import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("activities", "0012_map_dynamic_catalog")]

    operations = [
        migrations.RemoveConstraint(model_name="serviceentitlement", name="one_open_subscription_per_activity"),
        migrations.RemoveField(model_name="activitysession", name="activity_type"),
        migrations.RemoveField(model_name="activitysession", name="requires_exclusive_access"),
        migrations.RemoveField(model_name="serviceoffering", name="activity_type"),
        migrations.RemoveField(model_name="serviceoffering", name="catalog_item"),
        migrations.RemoveField(model_name="serviceoffering", name="grants_exclusive_access"),
        migrations.RemoveField(model_name="serviceentitlement", name="activity_type_snapshot"),
        migrations.RemoveField(model_name="serviceentitlement", name="grants_exclusive_access_snapshot"),
        migrations.AlterField(model_name="activitysession", name="catalog_type", field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sessions", to="catalog.catalogtype")),
        migrations.AlterField(model_name="serviceentitlement", name="catalog_type_name_snapshot", field=models.CharField(editable=False, max_length=120)),
        migrations.AlterField(model_name="serviceentitlement", name="catalog_type_reference", field=models.PositiveBigIntegerField(editable=False)),
        migrations.AlterField(model_name="serviceoffering", name="catalog_offer", field=models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="activity_offering", to="catalog.catalogoffer")),
        migrations.AlterModelOptions(name="serviceoffering", options={"ordering": ("catalog_offer__catalog_type__order", "catalog_offer__level__order", "mode", "catalog_offer__catalog_item__name")}),
        migrations.AddConstraint(
            model_name="serviceentitlement",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("offering_mode_snapshot", "subscription"),
                    ("status__in", ("pending", "active", "past_due", "cancel_at_period_end", "suspended")),
                ),
                fields=("customer", "pet", "catalog_type_reference"),
                name="one_open_subscription_per_activity",
            ),
        ),
    ]
