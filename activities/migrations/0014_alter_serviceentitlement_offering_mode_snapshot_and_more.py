import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('activities', '0013_remove_legacy_activity_classification'),
        ('hotel', '0009_bookingpriceadjustment_booking_confirmed_at_and_more'),
        ('pets', '0003_seed_dog_species'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='serviceentitlement',
            name='offering_mode_snapshot',
            field=models.CharField(choices=[('one_off', 'One-off purchase'), ('pack', 'Pack'), ('subscription', 'Subscription')], editable=False, max_length=20),
        ),
        migrations.AlterField(
            model_name='serviceoffering',
            name='mode',
            field=models.CharField(choices=[('one_off', 'One-off purchase'), ('pack', 'Pack'), ('subscription', 'Subscription')], db_index=True, max_length=20),
        ),
        migrations.CreateModel(
            name='ServiceFulfilment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('requested', 'Requested'), ('scheduled', 'Scheduled'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], db_index=True, default='requested', max_length=20)),
                ('preferred_date', models.DateField(blank=True, null=True)),
                ('preferred_time', models.TimeField(blank=True, null=True)),
                ('scheduled_starts_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('scheduled_ends_at', models.DateTimeField(blank=True, null=True)),
                ('customer_notes', models.TextField(blank=True)),
                ('internal_notes', models.TextField(blank=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('booking_service', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='fulfilment', to='hotel.bookingservice')),
                ('completed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='completed_service_fulfilments', to=settings.AUTH_USER_MODEL)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_service_fulfilments', to=settings.AUTH_USER_MODEL)),
                ('entitlement', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='fulfilments', to='activities.serviceentitlement')),
                ('pet', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='service_fulfilments', to='pets.pet')),
            ],
            options={
                'ordering': ('-created_at', '-id'),
            },
        ),
        migrations.CreateModel(
            name='ServiceFulfilmentEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('from_status', models.CharField(blank=True, max_length=20)),
                ('to_status', models.CharField(choices=[('requested', 'Requested'), ('scheduled', 'Scheduled'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], max_length=20)),
                ('reason', models.CharField(max_length=500)),
                ('details', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='service_fulfilment_events', to=settings.AUTH_USER_MODEL)),
                ('fulfilment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='events', to='activities.servicefulfilment')),
            ],
            options={
                'ordering': ('-created_at', '-id'),
            },
        ),
        migrations.AddConstraint(
            model_name='servicefulfilment',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('booking_service__isnull', True), ('entitlement__isnull', False)), models.Q(('booking_service__isnull', False), ('entitlement__isnull', True)), _connector='OR'), name='fulfilment_has_exactly_one_purchase_source'),
        ),
        migrations.AddConstraint(
            model_name='servicefulfilment',
            constraint=models.CheckConstraint(condition=models.Q(('scheduled_ends_at__isnull', True), ('scheduled_starts_at__isnull', False), _connector='OR'), name='fulfilment_end_requires_start'),
        ),
    ]
