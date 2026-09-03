from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from activities.models import OfferingMode
from activities.tests.factories import create_catalog_type, create_offering
from catalog.models import CatalogWorkflow
from discounts.forms import PromotionForm
from discounts.models import DiscountType, Promotion, SaleChannel
from hotel.tests.factories import create_account, create_pet


class PromotionFormTests(TestCase):
    def base_data(self, **overrides):
        data = {
            "code": "FORM10",
            "name": "Form promotion",
            "discount_type": DiscountType.PERCENTAGE,
            "value": "10",
            "eligible_sale_channels": [SaleChannel.DATE_RANGE],
            "is_active": "on",
        }
        data.update(overrides)
        return data

    def test_rejects_offer_outside_selected_catalog_type(self):
        accommodation = create_catalog_type(
            workflow=CatalogWorkflow.DATE_RANGE,
            slug="form-accommodation",
        )
        training = create_offering(
            workflow=CatalogWorkflow.TIMED_SLOT,
            mode=OfferingMode.PACK,
            remote_id="form-training",
        )

        form = PromotionForm(
            data=self.base_data(
                catalog_types=[accommodation.pk],
                catalog_offers=[training.catalog_offer_id],
            )
        )

        self.assertFalse(form.is_valid())
        self.assertIn("outside", form.errors["catalog_offers"][0])

    def test_accepts_offer_inside_selected_catalog_hierarchy(self):
        training = create_offering(
            workflow=CatalogWorkflow.TIMED_SLOT,
            mode=OfferingMode.PACK,
            remote_id="form-training-match",
        )

        form = PromotionForm(
            data=self.base_data(
                eligible_sale_channels=[SaleChannel.PACK],
                catalog_types=[training.catalog_type.pk],
                catalog_levels=[training.catalog_level.pk],
                catalog_offers=[training.catalog_offer_id],
            )
        )

        self.assertTrue(form.is_valid(), form.errors)

    def test_rejects_host_service_without_add_on_channel(self):
        host_type = create_catalog_type(
            workflow=CatalogWorkflow.DATE_RANGE,
            slug="form-host",
        )

        form = PromotionForm(data=self.base_data(host_catalog_types=[host_type.pk]))

        self.assertFalse(form.is_valid())
        self.assertIn("add-on", form.errors["host_catalog_types"][0])

    def test_rejects_a_named_dog_owned_by_a_different_selected_customer(self):
        selected_customer = create_account(username="selected-promotion-customer")
        other_customer = create_account(username="other-promotion-customer")
        other_pet = create_pet(other_customer)

        form = PromotionForm(
            data=self.base_data(
                customers=[selected_customer.pk],
                pets=[other_pet.pk],
            )
        )

        self.assertFalse(form.is_valid())
        self.assertIn("do not belong", form.errors["pets"][0])

    def test_datetime_local_values_round_trip_in_browser_format(self):
        form = PromotionForm(
            data=self.base_data(
                starts_at="2026-10-01T09:30",
                ends_at="2026-10-31T23:59",
            )
        )

        self.assertTrue(form.is_valid(), form.errors)
        promotion = form.save()
        rendered = PromotionForm(instance=promotion)
        self.assertIn("2026-10-01T09:30", rendered["starts_at"].as_widget())

    def test_fixed_amount_keeps_decimal_precision(self):
        form = PromotionForm(
            data=self.base_data(
                discount_type=DiscountType.FIXED,
                value="7.35",
            )
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().value, Decimal("7.35"))

    def test_new_form_does_not_preselect_every_purchase_flow(self):
        form = PromotionForm()

        self.assertFalse(form["eligible_sale_channels"].value())

    def test_service_dates_are_rejected_for_undated_pack_purchases(self):
        form = PromotionForm(
            data=self.base_data(
                eligible_sale_channels=[SaleChannel.PACK],
                service_starts_on="2026-10-01",
                service_ends_on="2026-10-31",
            )
        )

        self.assertFalse(form.is_valid())
        self.assertIn("separate promotion", form.errors["service_starts_on"][0])

    def test_service_dates_are_allowed_for_stays_and_dated_add_ons(self):
        form = PromotionForm(
            data=self.base_data(
                eligible_sale_channels=[SaleChannel.DATE_RANGE, SaleChannel.ADD_ON],
                service_starts_on="2026-10-01",
                service_ends_on="2026-10-31",
            )
        )

        self.assertTrue(form.is_valid(), form.errors)

    def test_searchable_audience_widget_renders_only_selected_customer(self):
        selected_customer = create_account(username="selected-widget-customer")
        unselected_customer = create_account(username="unselected-widget-customer")
        promotion = Promotion.objects.create(
            code="WIDGET",
            name="Search widget",
            discount_type=DiscountType.PERCENTAGE,
            value=Decimal("10"),
            eligible_sale_channels=[SaleChannel.DATE_RANGE],
        )
        promotion.customers.add(selected_customer)

        rendered = str(PromotionForm(instance=promotion)["customers"])

        self.assertIn(str(selected_customer.pk), rendered)
        self.assertIn(selected_customer.user.username, rendered)
        self.assertNotIn(unselected_customer.user.username, rendered)

    def test_breed_picker_values_are_normalized_and_saved(self):
        form = PromotionForm(
            data=self.base_data(eligible_breeds=[" Border Collie ", "border collie"])
        )

        self.assertTrue(form.is_valid(), form.errors)
        promotion = form.save()
        self.assertEqual(promotion.eligible_breeds, ["border collie"])

    def test_existing_inactive_restrictions_remain_editable(self):
        offering = create_offering(remote_id="inactive-promotion-offer")
        customer = create_account(username="inactive-promotion-customer")
        pet = create_pet(customer)
        promotion = Promotion.objects.create(
            code="FORM10",
            name="Form promotion",
            discount_type=DiscountType.PERCENTAGE,
            value=Decimal("10"),
            eligible_sale_channels=[SaleChannel.PACK],
        )
        promotion.catalog_types.add(offering.catalog_type)
        promotion.catalog_levels.add(offering.catalog_level)
        promotion.catalog_offers.add(offering.catalog_offer)
        promotion.customers.add(customer)
        promotion.pets.add(pet)
        offering.catalog_offer.is_active = False
        offering.catalog_offer.save(update_fields=("is_active", "updated_at"))
        offering.catalog_level.is_active = False
        offering.catalog_level.save(update_fields=("is_active", "updated_at"))
        offering.catalog_type.is_active = False
        offering.catalog_type.save(update_fields=("is_active", "updated_at"))
        customer.user.is_active = False
        customer.user.save(update_fields=("is_active",))
        pet.deleted_at = timezone.now()
        pet.save(update_fields=("deleted_at", "updated_at"))

        form = PromotionForm(instance=promotion)

        self.assertIn(offering.catalog_type, form.fields["catalog_types"].queryset)
        self.assertIn(offering.catalog_level, form.fields["catalog_levels"].queryset)
        self.assertIn(offering.catalog_offer, form.fields["catalog_offers"].queryset)
        self.assertIn(customer, form.fields["customers"].queryset)
        self.assertIn(pet, form.fields["pets"].queryset)
