import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_rename_account_customerprofile"),
        ("hotel", "0001_initial"),
    ]

    operations = [
        migrations.RenameField(
            model_name="booking",
            old_name="account",
            new_name="customer",
        ),
        migrations.AlterField(
            model_name="booking",
            name="customer",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="bookings",
                to="accounts.customerprofile",
            ),
        ),
    ]
