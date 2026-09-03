from django.db import migrations, models


def mark_existing_customer_projections(apps, schema_editor):
    CustomerProfile = apps.get_model("accounts", "CustomerProfile")
    CustomerProfile.objects.exclude(toconline_customer_id__isnull=True).exclude(
        toconline_customer_id=""
    ).update(toconline_sync_status="synced")


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_rename_account_customerprofile"),
    ]

    operations = [
        migrations.AddField(
            model_name="customerprofile",
            name="toconline_sync_attempted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="customerprofile",
            name="toconline_sync_error",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="customerprofile",
            name="toconline_sync_status",
            field=models.CharField(
                choices=[
                    ("pending", "Waiting to synchronize"),
                    ("synced", "Synchronized"),
                    ("failed", "Synchronization failed"),
                ],
                db_index=True,
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="customerprofile",
            name="toconline_synced_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(
            mark_existing_customer_projections,
            migrations.RunPython.noop,
        ),
    ]
