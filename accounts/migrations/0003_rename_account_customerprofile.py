from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_account_billing_phone_account_tax_number"),
        ("hotel", "0001_initial"),
        ("pets", "0002_remove_pet_is_allergic_remove_pet_is_medicated_and_more"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="Account",
            new_name="CustomerProfile",
        ),
        migrations.AlterField(
            model_name="customerprofile",
            name="user",
            field=models.OneToOneField(
                help_text="The authenticated user that owns this customer profile",
                on_delete=models.deletion.CASCADE,
                related_name="customer_profile",
                related_query_name="customer_profile",
                to="auth.user",
                verbose_name="user",
            ),
        ),
    ]
