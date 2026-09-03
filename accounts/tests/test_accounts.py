from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.forms import SignUpForm, UserChangeForm
from accounts.models import CustomerProfile, CustomerSyncStatus


class AccountFormTests(TestCase):
    def signup_data(self):
        return {
            "first_name": "Ana",
            "last_name": "Silva",
            "vat": "123456789",
            "phone": "912345678",
            "email": "ana@example.com",
            "username": "ana",
            "password1": "A-strong-test-password-123",
            "password2": "A-strong-test-password-123",
        }

    def test_signup_persists_local_billing_fields_needed_for_payment(self):
        form = SignUpForm(self.signup_data())

        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()

        self.assertEqual(user.customer_profile.billing_phone, "912345678")
        self.assertEqual(user.customer_profile.tax_number, "123456789")
        self.assertEqual(
            user.customer_profile.toconline_sync_status,
            CustomerSyncStatus.PENDING,
        )

    def test_commit_false_does_not_try_to_create_account_for_unsaved_user(self):
        form = SignUpForm(self.signup_data())
        self.assertTrue(form.is_valid(), form.errors)

        user = form.save(commit=False)

        self.assertIsNone(user.pk)
        self.assertEqual(CustomerProfile.objects.count(), 0)

    def test_change_form_loads_and_updates_local_billing_fields(self):
        user = get_user_model().objects.create_user(
            username="customer",
            password="test-password-123",
            email="old@example.com",
        )
        account = CustomerProfile.objects.create(
            user=user,
            billing_phone="911111111",
            tax_number="111111111",
        )
        form = UserChangeForm(
            {
                "username": "customer",
                "first_name": "New",
                "last_name": "Name",
                "vat": "222222222",
                "phone": "922222222",
                "email": "new@example.com",
            },
            instance=user,
        )

        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        account.refresh_from_db()
        self.assertEqual(account.billing_phone, "922222222")
        self.assertEqual(account.tax_number, "222222222")


class AccountViewTests(TestCase):
    def test_signup_queues_toconline_sync_without_calling_it_in_the_request(self):
        response = self.client.post(
            reverse("accounts:signup"),
            AccountFormTests().signup_data(),
        )

        self.assertRedirects(response, reverse("accounts:dashboard"))
        user = get_user_model().objects.get(username="ana")
        self.assertEqual(user.customer_profile.tax_number, "123456789")
        self.assertEqual(
            user.customer_profile.toconline_sync_status,
            CustomerSyncStatus.PENDING,
        )

    def test_profile_update_persists_locally_and_queues_toconline_sync(self):
        user = get_user_model().objects.create_user(
            username="customer",
            password="test-password-123",
            email="old@example.com",
        )
        account = CustomerProfile.objects.create(
            user=user,
            billing_phone="911111111",
            tax_number="111111111",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("accounts:user_change"),
            {
                "username": "customer",
                "first_name": "New",
                "last_name": "Name",
                "vat": "222222222",
                "phone": "922222222",
                "email": "new@example.com",
            },
        )

        self.assertRedirects(response, reverse("accounts:user_change_done"))
        account.refresh_from_db()
        self.assertEqual(account.billing_phone, "922222222")
        self.assertEqual(account.tax_number, "222222222")
        self.assertEqual(account.toconline_sync_status, CustomerSyncStatus.PENDING)

    def test_signup_returns_to_safe_purchase_destination(self):
        destination = "/activities/offers/42/purchase/"
        data = AccountFormTests().signup_data() | {"next": destination}

        response = self.client.post(reverse("accounts:signup"), data)

        self.assertRedirects(response, destination, fetch_redirect_response=False)

    def test_signup_rejects_external_return_destination(self):
        data = AccountFormTests().signup_data() | {"next": "https://attacker.example/checkout"}

        response = self.client.post(reverse("accounts:signup"), data)

        self.assertRedirects(response, reverse("accounts:dashboard"))

    def test_customer_dashboard_is_the_default_customer_home(self):
        user = get_user_model().objects.create_user(
            username="customer-dashboard",
            password="test-password-123",
        )
        CustomerProfile.objects.create(user=user)
        self.client.force_login(user)

        response = self.client.get(reverse("accounts:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hotel bookings")
        self.assertContains(response, "Daycare and training")

    def test_staff_dashboard_route_redirects_to_management(self):
        staff = get_user_model().objects.create_user(
            username="staff-dashboard",
            password="test-password-123",
            is_staff=True,
        )
        self.client.force_login(staff)

        response = self.client.get(reverse("accounts:dashboard"))

        self.assertRedirects(response, reverse("backoffice:dashboard"))
