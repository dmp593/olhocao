import activities.models
import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("activities", "0010_serviceentitlement_promotion_and_more"),
        ("catalog", "0002_seed_catalog_hierarchy"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="OperationalClosure",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=150)),
                ("source", models.CharField(choices=[("manual", "Manual closure"), ("municipal_holiday", "Municipal or optional holiday")], default="manual", max_length=24)),
                ("starts_at", models.DateTimeField(db_index=True)),
                ("ends_at", models.DateTimeField(db_index=True)),
                ("all_operational_services", models.BooleanField(default=False, help_text="Apply to every non-accommodation service. Date-range accommodation always remains available online.")),
                ("reason", models.CharField(max_length=500)),
                ("customer_message", models.CharField(blank=True, max_length=240)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("catalog_types", models.ManyToManyField(blank=True, related_name="closures", to="catalog.catalogtype")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_operational_closures", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("starts_at", "id")},
        ),
        migrations.CreateModel(
            name="Trainer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=150)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("phone", models.CharField(blank=True, max_length=32)),
                ("biography", models.TextField(blank=True)),
                ("image", models.ImageField(blank=True, upload_to=activities.models.trainer_image_upload_to, validators=[django.core.validators.FileExtensionValidator(allowed_extensions=("jpeg", "jpg", "png", "webp"))])),
                ("image_alt", models.CharField(blank=True, max_length=180)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="trainer_profile", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("name", "id")},
        ),
        migrations.CreateModel(
            name="SessionSeries",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=150)),
                ("starts_on", models.DateField()),
                ("ends_on", models.DateField()),
                ("weekdays", models.JSONField(default=list, help_text="ISO weekdays from 1 (Monday) to 7 (Sunday).")),
                ("starts_at_time", models.TimeField()),
                ("ends_at_time", models.TimeField()),
                ("capacity_override", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("skip_closed_days", models.BooleanField(default=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("catalog_type", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="session_series", to="catalog.catalogtype")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_activity_session_series", to=settings.AUTH_USER_MODEL)),
                ("eligible_levels", models.ManyToManyField(blank=True, related_name="session_series", to="catalog.cataloglevel")),
                ("trainers", models.ManyToManyField(blank=True, related_name="session_series", to="activities.trainer")),
            ],
            options={"ordering": ("starts_on", "starts_at_time", "id")},
        ),
        migrations.CreateModel(
            name="WeeklyUsageAdjustment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("week_start", models.DateField(help_text="Monday of the week receiving extra uses.")),
                ("extra_uses", models.PositiveSmallIntegerField()),
                ("reason", models.CharField(max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="activity_weekly_usage_adjustments", to=settings.AUTH_USER_MODEL)),
                ("entitlement", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="weekly_usage_adjustments", to="activities.serviceentitlement")),
            ],
            options={"ordering": ("-week_start", "-created_at", "-id")},
        ),
        migrations.AddField(model_name="activityreservation", name="cancellation_policy_overridden", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="activityreservation", name="cancellation_reason", field=models.CharField(blank=True, max_length=500)),
        migrations.AddField(model_name="activitysession", name="catalog_type", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="sessions", to="catalog.catalogtype")),
        migrations.AddField(model_name="activitysession", name="eligible_levels", field=models.ManyToManyField(blank=True, help_text="Leave empty to accept every active level in this catalog type.", related_name="sessions", to="catalog.cataloglevel")),
        migrations.AddField(model_name="activitysession", name="source_series", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="sessions", to="activities.sessionseries")),
        migrations.AddField(model_name="activitysession", name="trainers", field=models.ManyToManyField(blank=True, related_name="sessions", to="activities.trainer")),
        migrations.AddField(model_name="serviceentitlement", name="catalog_level_name_snapshot", field=models.CharField(blank=True, editable=False, max_length=120)),
        migrations.AddField(model_name="serviceentitlement", name="catalog_level_reference", field=models.PositiveBigIntegerField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name="serviceentitlement", name="catalog_type_name_snapshot", field=models.CharField(blank=True, editable=False, max_length=120)),
        migrations.AddField(model_name="serviceentitlement", name="catalog_type_reference", field=models.PositiveBigIntegerField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name="serviceoffering", name="catalog_offer", field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="activity_offering", to="catalog.catalogoffer")),
        migrations.AlterField(model_name="activityreservation", name="status", field=models.CharField(choices=[("booked", "Booked"), ("attended", "Attended"), ("cancelled", "Cancelled"), ("late_cancelled", "Cancelled after deadline"), ("no_show", "No-show")], db_index=True, default="booked", max_length=20)),
        migrations.AlterField(model_name="activityreservationevent", name="to_status", field=models.CharField(choices=[("booked", "Booked"), ("attended", "Attended"), ("cancelled", "Cancelled"), ("late_cancelled", "Cancelled after deadline"), ("no_show", "No-show")], max_length=20)),
        migrations.AddIndex(model_name="operationalclosure", index=models.Index(fields=["is_active", "starts_at", "ends_at"], name="closure_active_range_idx")),
        migrations.AddConstraint(model_name="operationalclosure", constraint=models.CheckConstraint(condition=models.Q(("ends_at__gt", models.F("starts_at"))), name="operational_closure_has_positive_duration")),
        migrations.AddConstraint(model_name="weeklyusageadjustment", constraint=models.CheckConstraint(condition=models.Q(("extra_uses__gte", 1)), name="weekly_usage_adjustment_is_positive")),
        migrations.AddConstraint(model_name="activitysession", constraint=models.UniqueConstraint(condition=models.Q(("source_series__isnull", False)), fields=("source_series", "starts_at"), name="unique_materialized_activity_session")),
    ]
