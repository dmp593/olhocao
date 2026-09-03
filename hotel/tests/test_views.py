from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.templatetags.static import static
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from activities.models import OfferingMode, ServiceOffering
from catalog.models import AddOnAvailability
from hotel.models import BookingStatus, CatalogBookingKind
from hotel.tests.factories import (
    create_account,
    create_booking,
    create_catalog_item,
    create_catalog_offer,
    create_pet,
)


class BookingAccessTests(TestCase):
    def setUp(self):
        self.account = create_account()
        self.booking, _ = create_booking(self.account)
        self.other_account = create_account(username="other")

    def test_customer_cannot_read_another_customers_booking(self):
        self.client.force_login(self.other_account.user)

        response = self.client.get(reverse("hotel:booking_detail", kwargs={"pk": self.booking.pk}))

        self.assertEqual(response.status_code, 404)

    def test_staff_can_read_any_booking(self):
        staff = create_account(username="staff", staff=True)
        self.client.force_login(staff.user)

        response = self.client.get(reverse("hotel:booking_detail", kwargs={"pk": self.booking.pk}))

        self.assertEqual(response.status_code, 200)

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("hotel:booking_list"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_user_without_customer_profile_is_sent_to_profile_form(self):
        user = get_user_model().objects.create_user(
            username="incomplete-customer",
            password="test-password-123",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("hotel:booking_list"))

        self.assertRedirects(response, reverse("accounts:user_change"))

    def test_user_without_customer_profile_cannot_read_a_booking(self):
        user = get_user_model().objects.create_user(
            username="profileless-customer",
            password="test-password-123",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("hotel:booking_detail", kwargs={"pk": self.booking.pk}))

        self.assertEqual(response.status_code, 404)


class BookingWizardTests(TestCase):
    def setUp(self):
        self.account = create_account()
        self.pet = create_pet(self.account)
        self.stay = create_catalog_item()
        self.extra = create_catalog_item(
            remote_id="extra-1",
            name="Walk",
            price="5.00",
            booking_kind=CatalogBookingKind.EXTRA,
        )
        self.stay_offer = create_catalog_offer(self.stay)
        self.extra_offer = create_catalog_offer(self.extra)
        ServiceOffering.objects.create(
            catalog_offer=self.extra_offer,
            mode=OfferingMode.ONE_OFF,
            cancellation_notice_hours=24,
            is_published=True,
        )
        AddOnAvailability.objects.create(
            add_on=self.extra_offer,
            target_type=self.stay_offer.catalog_type,
        )
        self.client.force_login(self.account.user)

    def test_complete_wizard_creates_booking_and_redirects_to_hosted_payment(self):
        start_date = date.today() + timedelta(days=14)
        end_date = start_date + timedelta(days=3)

        response = self.client.post(
            reverse("hotel:booking_stay"),
            {
                "offer_id": self.stay_offer.pk,
                "pet_ids": [self.pet.pk],
                "start_date": start_date,
                "end_date": end_date,
            },
        )
        self.assertRedirects(response, reverse("hotel:booking_services"))

        service_field = f"pet-{self.pet.pk}-offer-{self.extra_offer.pk}-quantity"
        response = self.client.post(
            reverse("hotel:booking_services"),
            {service_field: 2},
        )
        self.assertRedirects(response, reverse("hotel:booking_review"))
        self.assertEqual(self.client.get(reverse("hotel:booking_review")).status_code, 200)

        fake_payment = SimpleNamespace(payment_url="https://pay.example/booking")
        with patch(
            "hotel.booking_views.create_payment_attempt",
            return_value=fake_payment,
        ):
            response = self.client.post(
                reverse("hotel:booking_confirm"),
                {"terms_accepted": True, "special_requests": "Quiet area"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "https://pay.example/booking")
        booking = self.account.bookings.get()
        self.assertEqual(booking.notes, "Quiet area")
        self.assertEqual(booking.total_price_eur, 70)

    @patch("hotel.booking_views.create_payment_attempt")
    def test_assisted_booking_stops_before_creating_a_payment_link(
        self,
        create_payment,
    ):
        staff = create_account(username="assisted-payment-staff", staff=True)
        self.client.force_login(staff.user)
        session = self.client.session
        session["assisted_customer_id"] = self.account.pk
        session.save()

        start_date = date.today() + timedelta(days=14)
        response = self.client.post(
            reverse("hotel:booking_stay"),
            {
                "offer_id": self.stay_offer.pk,
                "pet_ids": [self.pet.pk],
                "start_date": start_date,
                "end_date": start_date + timedelta(days=1),
            },
        )
        self.assertRedirects(response, reverse("hotel:booking_services"))
        response = self.client.post(reverse("hotel:booking_services"), {})
        self.assertRedirects(response, reverse("hotel:booking_review"))

        response = self.client.post(
            reverse("hotel:booking_confirm"),
            {"terms_accepted": True, "special_requests": ""},
        )

        booking = self.account.bookings.get()
        self.assertRedirects(
            response,
            reverse("hotel:booking_detail", args=(booking.pk,)),
        )
        create_payment.assert_not_called()
        self.assertNotIn("assisted_customer_id", self.client.session)
        self.assertNotIn("booking_data", self.client.session)

    def test_review_rejects_catalog_item_removed_after_first_step(self):
        session = self.client.session
        session["booking_data"] = {
            "offer_id": str(self.stay_offer.pk),
            "pet_ids": [str(self.pet.pk)],
            "start_date": (date.today() + timedelta(days=14)).isoformat(),
            "end_date": (date.today() + timedelta(days=17)).isoformat(),
        }
        session.save()
        self.stay.is_active = False
        self.stay.save(update_fields=("is_active",))

        response = self.client.get(reverse("hotel:booking_review"))

        self.assertEqual(response.status_code, 404)

    def test_one_night_booking_is_valid(self):
        start_date = date.today() + timedelta(days=14)

        response = self.client.post(
            reverse("hotel:booking_stay"),
            {
                "offer_id": self.stay_offer.pk,
                "pet_ids": [self.pet.pk],
                "start_date": start_date,
                "end_date": start_date + timedelta(days=1),
            },
        )

        self.assertRedirects(response, reverse("hotel:booking_services"))
        self.assertEqual(
            self.client.session["booking_data"]["end_date"],
            str(start_date + timedelta(days=1)),
        )

    def test_review_shows_species_and_breed_for_each_dog(self):
        session = self.client.session
        session["booking_data"] = {
            "offer_id": str(self.stay_offer.pk),
            "pet_ids": [str(self.pet.pk)],
            "start_date": (date.today() + timedelta(days=14)).isoformat(),
            "end_date": (date.today() + timedelta(days=15)).isoformat(),
        }
        session.save()

        response = self.client.get(reverse("hotel:booking_review"))

        self.assertContains(response, "Dog • Mixed")

    def test_staff_can_book_for_selected_customer(self):
        staff = create_account(username="staff", staff=True)
        self.client.force_login(staff.user)
        session = self.client.session
        session["assisted_customer_id"] = self.account.pk
        session.save()

        response = self.client.get(reverse("hotel:booking_stay"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["pets"]), [self.pet])

    def test_assisted_review_explains_price_review_before_payment_link(self):
        staff = create_account(username="review-staff", staff=True)
        self.client.force_login(staff.user)
        session = self.client.session
        session["assisted_customer_id"] = self.account.pk
        session["booking_data"] = {
            "offer_id": str(self.stay_offer.pk),
            "pet_ids": [str(self.pet.pk)],
            "start_date": (date.today() + timedelta(days=14)).isoformat(),
            "end_date": (date.today() + timedelta(days=15)).isoformat(),
        }
        session.save()

        response = self.client.get(reverse("hotel:booking_review"))

        self.assertContains(response, "Create booking for review")
        self.assertContains(response, "review or adjust the final price")
        self.assertNotContains(response, "Confirm &amp; Pay Now")


class BookingManagementViewTests(TestCase):
    def setUp(self):
        self.account = create_account()
        self.booking, self.stay = create_booking(self.account, duration=3)
        self.client.force_login(self.account.user)

    def test_reschedule_preserves_duration_for_every_pet_stay(self):
        second_pet = create_pet(self.account, name="Luna")
        _, second_stay = create_booking(
            self.account,
            stay_item=self.stay.catalog_item,
            pet=second_pet,
            duration=3,
        )
        second_stay.booking = self.booking
        second_stay.save(update_fields=("booking",))
        new_start = date.today() + timedelta(days=30)

        response = self.client.post(
            reverse("hotel:booking_modify", kwargs={"booking_id": self.booking.pk}),
            {"start_date": new_start},
        )

        self.assertRedirects(
            response,
            reverse("hotel:booking_detail", kwargs={"pk": self.booking.pk}),
        )
        for stay in self.booking.stays.all():
            self.assertEqual(stay.start_date, new_start)
            self.assertEqual(stay.end_date, new_start + timedelta(days=3))

    def test_direct_reschedule_is_blocked_inside_policy_window(self):
        self.stay.start_date = date.today() + timedelta(days=2)
        self.stay.end_date = date.today() + timedelta(days=5)
        self.stay.save(update_fields=("start_date", "end_date"))

        response = self.client.post(
            reverse("hotel:booking_modify", kwargs={"booking_id": self.booking.pk}),
            {"start_date": date.today() + timedelta(days=20)},
        )

        self.assertRedirects(
            response,
            reverse("hotel:booking_detail", kwargs={"pk": self.booking.pk}),
        )
        self.stay.refresh_from_db()
        self.assertEqual(self.stay.start_date, date.today() + timedelta(days=2))

    def test_cancel_confirmation_contains_csrf_protection(self):
        response = self.client.get(
            reverse("hotel:booking_cancel_confirm", kwargs={"booking_id": self.booking.pk})
        )

        self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_staff_can_reschedule_inside_customer_policy_window(self):
        self.stay.start_date = date.today() + timedelta(days=2)
        self.stay.end_date = date.today() + timedelta(days=5)
        self.stay.save(update_fields=("start_date", "end_date"))
        staff = create_account(username="reschedule-staff", staff=True)
        self.client.force_login(staff.user)
        new_start = date.today() + timedelta(days=20)

        response = self.client.post(
            reverse("hotel:booking_modify", kwargs={"booking_id": self.booking.pk}),
            {"start_date": new_start},
        )

        self.assertRedirects(
            response,
            reverse("hotel:booking_detail", kwargs={"pk": self.booking.pk}),
        )
        self.stay.refresh_from_db()
        self.assertEqual(self.stay.start_date, new_start)
        self.assertEqual(self.stay.end_date, new_start + timedelta(days=3))

    def test_pending_booking_detail_links_to_payment_retry(self):
        response = self.client.get(reverse("hotel:booking_detail", kwargs={"pk": self.booking.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse(
                "hotel:booking_retry",
                kwargs={"booking_id": self.booking.pk},
            ),
        )

    def test_cancelled_booking_detail_has_no_payment_action(self):
        self.booking.status = BookingStatus.CANCELLED
        self.booking.save(update_fields=("status", "updated_at"))
        self.booking.payment_attempts.create(
            merchant_reference="cancelled-booking-stale-link",
            payment_url="https://pay.example/stale-link",
            amount_eur=self.booking.total_price_eur,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        response = self.client.get(reverse("hotel:booking_detail", args=(self.booking.pk,)))

        self.assertContains(response, "No further payment is required")
        self.assertNotContains(response, "Complete Payment")
        self.assertNotContains(response, "Try payment again")

    def test_staff_booking_detail_never_links_directly_to_hosted_payment(self):
        payment = self.booking.payment_attempts.create(
            merchant_reference="staff-must-not-pay",
            payment_url="https://pay.example/customer-only",
            amount_eur=self.booking.total_price_eur,
            expires_at=timezone.now() + timedelta(days=1),
        )
        staff = create_account(username="payment-staff", staff=True)
        self.client.force_login(staff.user)

        response = self.client.get(reverse("hotel:booking_detail", kwargs={"pk": self.booking.pk}))

        self.assertContains(response, "Send link for payment")
        self.assertNotContains(response, payment.payment_url)

    def test_staff_retry_explains_that_customer_receives_and_pays_from_email(self):
        payment = self.booking.payment_attempts.create(
            merchant_reference="staff-email-explanation",
            payment_url="https://pay.example/customer-only",
            amount_eur=self.booking.total_price_eur,
            expires_at=timezone.now() + timedelta(days=1),
        )
        staff = create_account(username="payment-delivery-staff", staff=True)
        self.client.force_login(staff.user)

        response = self.client.get(
            reverse("hotel:booking_retry", kwargs={"booking_id": self.booking.pk})
        )

        self.assertContains(response, "Send link for payment")
        self.assertContains(response, self.booking.customer.user.email)
        self.assertNotContains(response, payment.payment_url)

    def test_retry_is_blocked_after_the_booking_start_date(self):
        self.stay.start_date = date.today() - timedelta(days=1)
        self.stay.end_date = date.today() + timedelta(days=1)
        self.stay.save(update_fields=("start_date", "end_date"))

        response = self.client.get(
            reverse("hotel:booking_retry", kwargs={"booking_id": self.booking.pk})
        )

        self.assertRedirects(response, reverse("hotel:booking_list"))

    @patch("hotel.booking_views.send_booking_payment_link_email")
    @patch("hotel.booking_views.create_payment_attempt")
    def test_staff_retry_emails_link_instead_of_opening_checkout(self, create, send):
        payment = self.booking.payment_attempts.create(
            merchant_reference="staff-email-link",
            payment_url="https://pay.example/customer-only",
            amount_eur=self.booking.total_price_eur,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        create.return_value = payment
        staff = create_account(username="retry-staff", staff=True)
        self.client.force_login(staff.user)

        response = self.client.post(
            reverse("hotel:booking_retry", kwargs={"booking_id": self.booking.pk})
        )

        self.assertRedirects(
            response,
            reverse("hotel:booking_detail", kwargs={"pk": self.booking.pk}),
        )
        send.assert_called_once_with(payment)
        self.assertFalse(create.call_args.kwargs["notify_customer"])

    @patch("hotel.booking_views.cancel_booking")
    def test_cancellation_post_uses_domain_service(self, cancel_booking):
        cancel_booking.return_value = SimpleNamespace(
            refund_error="",
        )

        response = self.client.post(
            reverse("hotel:booking_cancel", kwargs={"booking_id": self.booking.pk})
        )

        self.assertRedirects(
            response,
            reverse("hotel:booking_detail", kwargs={"pk": self.booking.pk}),
        )
        cancel_booking.assert_called_once_with(
            self.booking,
            actor=self.account.user,
            enforce_customer_policy=True,
        )

    @patch("hotel.booking_views.cancel_booking")
    def test_staff_cancellation_bypasses_customer_notice_window(self, cancel_booking):
        cancel_booking.return_value = SimpleNamespace(refund_error="")
        staff = create_account(username="cancellation-staff", staff=True)
        self.client.force_login(staff.user)

        self.client.post(reverse("hotel:booking_cancel", kwargs={"booking_id": self.booking.pk}))

        cancel_booking.assert_called_once_with(
            self.booking,
            actor=staff.user,
            enforce_customer_policy=False,
        )


class PublicCatalogViewTests(TestCase):
    def test_pricing_page_reads_local_projection_without_toconline_request(self):
        catalog_item = create_catalog_item()
        catalog_item.is_public = True
        catalog_item.save(update_fields=("is_public",))
        create_catalog_offer(catalog_item)

        with patch(
            "olhocao.integrations.toconline.TocOnlineGateway.list_services",
            side_effect=AssertionError("remote request was not expected"),
        ):
            response = self.client.get(reverse("frontoffice:pricing"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hotel")
        self.assertContains(response, static("images/services/hotel-stay.webp"))

    def test_home_uses_local_generated_artwork(self):
        response = self.client.get(reverse("frontoffice:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, static("images/services/park-play.webp"))
        self.assertContains(response, static("images/testimonials/golden-retriever.webp"))
        self.assertNotContains(response, "placedog.net")
        self.assertNotIn("placedog.net", response["Content-Security-Policy"])


class HealthCheckTests(TestCase):
    def test_liveness_and_readiness_are_available(self):
        liveness = self.client.get(reverse("health_live"))

        self.assertEqual(liveness.json(), {"status": "ok"})
        self.assertEqual(self.client.get(reverse("health_ready")).json(), {"status": "ok"})
        self.assertIn("default-src 'self'", liveness["Content-Security-Policy"])
        self.assertEqual(
            liveness["Permissions-Policy"],
            "camera=(), geolocation=(), microphone=()",
        )

    def test_inline_scripts_require_a_per_response_csp_nonce(self):
        response = self.client.get(reverse("frontoffice:home"))
        policy = response["Content-Security-Policy"]
        nonce = policy.split("'nonce-", 1)[1].split("'", 1)[0]

        second_response = self.client.get(reverse("frontoffice:home"))
        second_policy = second_response["Content-Security-Policy"]
        second_nonce = second_policy.split("'nonce-", 1)[1].split("'", 1)[0]

        self.assertIn("script-src 'self' 'nonce-", policy)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", policy)
        self.assertContains(response, f'nonce="{nonce}"', count=2)
        self.assertContains(second_response, f'nonce="{second_nonce}"', count=2)
        self.assertNotEqual(nonce, second_nonce)

    def test_request_id_is_preserved_only_when_safe(self):
        response = self.client.get(reverse("health_live"), headers={"X-Request-ID": "job-42"})
        unsafe = self.client.get(
            reverse("health_live"),
            headers={"X-Request-ID": "invalid request id with spaces"},
        )

        self.assertEqual(response["X-Request-ID"], "job-42")
        self.assertNotEqual(unsafe["X-Request-ID"], "invalid request id with spaces")
        self.assertEqual(len(unsafe["X-Request-ID"]), 32)

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_internal_health_checks_are_exempt_from_https_redirect(self):
        self.assertEqual(self.client.get(reverse("health_live")).status_code, 200)
        self.assertEqual(self.client.get(reverse("health_ready")).status_code, 200)
        self.assertEqual(self.client.get(reverse("frontoffice:home")).status_code, 301)
