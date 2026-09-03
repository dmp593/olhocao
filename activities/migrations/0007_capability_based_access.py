from django.db import migrations, models


def migrate_tiers_to_capabilities(apps, schema_editor):
    ActivitySession = apps.get_model("activities", "ActivitySession")
    ServiceEntitlement = apps.get_model("activities", "ServiceEntitlement")
    ServiceOffering = apps.get_model("activities", "ServiceOffering")

    ServiceOffering.objects.filter(tier="premium").update(grants_exclusive_access=True)
    ActivitySession.objects.filter(tier="premium").update(requires_exclusive_access=True)

    for entitlement in ServiceEntitlement.objects.select_related(
        "offering__catalog_item"
    ).iterator():
        item = entitlement.offering.catalog_item
        ServiceEntitlement.objects.filter(pk=entitlement.pk).update(
            grants_exclusive_access_snapshot=(entitlement.tier_snapshot == "premium"),
            item_code_snapshot=item.item_code,
            tax_code_snapshot=item.tax_code,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("activities", "0006_service_entitlement_contract_snapshots"),
        ("hotel", "0006_catalog_authority_and_fiscal_snapshots"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="serviceoffering",
            options={
                "ordering": (
                    "activity_type",
                    "grants_exclusive_access",
                    "mode",
                    "catalog_item__name",
                )
            },
        ),
        migrations.AddField(
            model_name="activitysession",
            name="requires_exclusive_access",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Only services with the exclusive-access capability can book this session."
                ),
            ),
        ),
        migrations.AddField(
            model_name="serviceentitlement",
            name="grants_exclusive_access_snapshot",
            field=models.BooleanField(editable=False, null=True),
        ),
        migrations.AddField(
            model_name="serviceentitlement",
            name="item_code_snapshot",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="serviceentitlement",
            name="tax_code_snapshot",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="serviceoffering",
            name="grants_exclusive_access",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Allows this service to book sessions marked as exclusive. "
                    "This capability is independent from the commercial product name."
                ),
            ),
        ),
        migrations.RunPython(migrate_tiers_to_capabilities, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="serviceentitlement",
            name="grants_exclusive_access_snapshot",
            field=models.BooleanField(editable=False),
        ),
        migrations.RemoveField(
            model_name="activitysession",
            name="tier",
        ),
        migrations.RemoveField(
            model_name="serviceentitlement",
            name="tier_snapshot",
        ),
        migrations.RemoveField(
            model_name="serviceoffering",
            name="tier",
        ),
    ]
