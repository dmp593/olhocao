from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from discounts.models import Promotion, PromotionChangeType, SaleChannel
from hotel.tests.factories import create_account, create_pet


class PromotionManagementViewTests(TestCase):
    def setUp(self):
        self.staff = create_account(username="promotion-staff", staff=True)
        self.customer = create_account(username="promotion-customer")
        self.list_url = reverse("discounts:promotion_list")

    def promotion_payload(self, **overrides):
        values = {
            "code": "summer-10",
            "name": "Summer care",
            "discount_type": "percentage",
            "value": "10",
            "eligible_sale_channels": [SaleChannel.DATE_RANGE],
            "eligible_breeds": "",
            "starts_at": "",
            "ends_at": "",
            "service_starts_on": "",
            "service_ends_on": "",
            "minimum_eligible_amount_eur": "",
            "max_redemptions": "",
            "max_redemptions_per_customer": "",
            "is_active": "on",
        }
        values.update(overrides)
        return values

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_customer_cannot_open_management(self):
        self.client.force_login(self.customer.user)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 403)

    def test_audience_search_is_staff_only_and_matches_customer_details(self):
        search_url = reverse(
            "discounts:promotion_audience_search",
            args=("customers",),
        )
        self.client.force_login(self.customer.user)
        self.assertEqual(self.client.get(search_url).status_code, 403)

        self.client.force_login(self.staff.user)
        response = self.client.get(search_url, {"q": self.customer.user.email})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["id"], self.customer.pk)
        self.assertEqual(
            response.json()["results"][0]["description"],
            self.customer.user.email,
        )

    def test_audience_search_paginates_large_customer_lists(self):
        for index in range(25):
            create_account(username=f"search-customer-{index:02d}")
        self.client.force_login(self.staff.user)

        response = self.client.get(
            reverse(
                "discounts:promotion_audience_search",
                args=("customers",),
            ),
            {"q": "search-customer", "page": 1},
        )

        payload = response.json()
        self.assertEqual(len(payload["results"]), 20)
        self.assertEqual(payload["next_page"], 2)

    def test_breed_search_returns_existing_breeds_without_case_duplicates(self):
        first_pet = create_pet(self.customer, name="First breed dog")
        first_pet.breed = "Border Collie"
        first_pet.save(update_fields=("breed", "updated_at"))
        second_customer = create_account(username="second-breed-owner")
        second_pet = create_pet(second_customer, name="Second breed dog")
        second_pet.breed = "border collie"
        second_pet.save(update_fields=("breed", "updated_at"))
        self.client.force_login(self.staff.user)

        response = self.client.get(
            reverse("discounts:promotion_audience_search", args=("breeds",)),
            {"q": "collie"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["results"]), 1)
        self.assertEqual(response.json()["results"][0]["label"].casefold(), "border collie")

    def test_staff_can_create_an_audited_promotion(self):
        self.client.force_login(self.staff.user)

        response = self.client.post(
            reverse("discounts:promotion_create"),
            self.promotion_payload(),
        )

        promotion = Promotion.objects.get()
        self.assertRedirects(
            response,
            reverse("discounts:promotion_update", kwargs={"pk": promotion.pk}),
        )
        self.assertEqual(promotion.code, "SUMMER-10")
        event = promotion.change_events.get()
        self.assertEqual(event.change_type, PromotionChangeType.CREATED)
        self.assertEqual(event.actor, self.staff.user)

    def test_staff_update_preserves_a_change_history(self):
        promotion = Promotion.objects.create(
            code="CARE",
            name="Customer care",
            discount_type="fixed",
            value=Decimal("5.00"),
            eligible_sale_channels=[SaleChannel.DATE_RANGE],
        )
        self.client.force_login(self.staff.user)

        response = self.client.post(
            reverse("discounts:promotion_update", kwargs={"pk": promotion.pk}),
            self.promotion_payload(
                code="CARE",
                name="Updated customer care",
                discount_type="fixed",
                value="7.50",
            ),
        )

        self.assertRedirects(
            response,
            reverse("discounts:promotion_update", kwargs={"pk": promotion.pk}),
        )
        event = promotion.change_events.get(change_type=PromotionChangeType.UPDATED)
        self.assertEqual(event.details["changes"]["name"]["from"], "Customer care")
        self.assertEqual(event.details["changes"]["name"]["to"], "Updated customer care")

    def test_staff_can_deactivate_without_deleting_history(self):
        promotion = Promotion.objects.create(
            code="PAUSE",
            name="Temporary offer",
            discount_type="fixed",
            value=Decimal("2.00"),
            eligible_sale_channels=[SaleChannel.DATE_RANGE],
        )
        self.client.force_login(self.staff.user)

        response = self.client.post(
            reverse("discounts:promotion_toggle", kwargs={"pk": promotion.pk})
        )

        self.assertRedirects(response, self.list_url)
        promotion.refresh_from_db()
        self.assertFalse(promotion.is_active)
        self.assertTrue(
            promotion.change_events.filter(
                change_type=PromotionChangeType.DEACTIVATED,
                actor=self.staff.user,
            ).exists()
        )
