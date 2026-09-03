from django.db import migrations, models


def populate_contract_snapshots(apps, schema_editor):
    ServiceEntitlement = apps.get_model("activities", "ServiceEntitlement")
    for entitlement in ServiceEntitlement.objects.select_related("offering").iterator():
        offering = entitlement.offering
        ServiceEntitlement.objects.filter(pk=entitlement.pk).update(
            tier_snapshot=offering.tier,
            included_sessions_snapshot=offering.included_sessions,
            sessions_per_week_snapshot=offering.sessions_per_week,
            validity_days_snapshot=offering.validity_days,
            billing_frequency_snapshot=offering.billing_frequency,
            cancellation_notice_hours_snapshot=offering.cancellation_notice_hours,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("activities", "0005_alter_serviceentitlement_activity_type_snapshot_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="serviceentitlement",
            name="tier_snapshot",
            field=models.CharField(
                blank=True,
                choices=[("standard", "Standard"), ("premium", "Premium")],
                editable=False,
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="serviceentitlement",
            name="included_sessions_snapshot",
            field=models.PositiveSmallIntegerField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="serviceentitlement",
            name="sessions_per_week_snapshot",
            field=models.PositiveSmallIntegerField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="serviceentitlement",
            name="validity_days_snapshot",
            field=models.PositiveSmallIntegerField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="serviceentitlement",
            name="billing_frequency_snapshot",
            field=models.CharField(
                blank=True,
                choices=[
                    ("1W", "Weekly"),
                    ("2W", "Every two weeks"),
                    ("1M", "Monthly"),
                    ("2M", "Every two months"),
                    ("3M", "Quarterly"),
                    ("6M", "Every six months"),
                    ("1Y", "Yearly"),
                ],
                editable=False,
                max_length=3,
            ),
        ),
        migrations.AddField(
            model_name="serviceentitlement",
            name="cancellation_notice_hours_snapshot",
            field=models.PositiveSmallIntegerField(editable=False, null=True),
        ),
        migrations.RunPython(populate_contract_snapshots, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="serviceentitlement",
            name="tier_snapshot",
            field=models.CharField(
                choices=[("standard", "Standard"), ("premium", "Premium")],
                editable=False,
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="serviceentitlement",
            name="cancellation_notice_hours_snapshot",
            field=models.PositiveSmallIntegerField(editable=False),
        ),
    ]
