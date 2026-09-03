import base64
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomerProfile
from activities.models import (
    ActivitySession,
    ActivitySessionEvent,
    CreditLedgerEntry,
    EntitlementStatus,
    LedgerReason,
    OfferingMode,
    OperationalClosure,
    ReservationStatus,
    SubscriptionManagementAction,
    SubscriptionManagementEvent,
    Trainer,
)
from activities.services.calendar import trainers_have_conflict
from activities.services.reservations import reserve_session
from activities.tests.factories import create_offering, create_session
from backoffice.catalog_forms import CatalogOfferForm
from backoffice.forms import (
    ActivitySessionForm,
    HomeSlideForm,
    PartnerForm,
    SiteSettingsForm,
    SocialLinkForm,
)
from backoffice.models import (
    LegalDocument,
    LegalDocumentSection,
    LegalDocumentSectionLineItem,
)
from backoffice.services.bookings import filtered_booking_stays
from catalog.models import (
    AddOnAvailability,
    CatalogLevel,
    CatalogOffer,
    CatalogType,
    CatalogWorkflow,
)
from frontoffice.models import HomeSlide, HomeSlideKind, ServiceHighlight, SiteSettings
from hotel.models import (
    BookingStatus,
    CatalogItemOrigin,
    CatalogItemType,
    CatalogSyncStatus,
)
from hotel.tests.factories import create_account, create_booking, create_catalog_item, create_pet


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    PUBLIC_BASE_URL="https://www.olhocao.pt",
)
class CustomerInvitationTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="manager",
            password="test-password-123",
            is_staff=True,
        )
        self.client.force_login(self.staff)

    def test_staff_creates_customer_without_temporary_password_and_sends_invitation(self):
        response = self.client.post(
            reverse("backoffice:user_create"),
            {
                "first_name": "Ana",
                "last_name": "Silva",
                "email": "ana@example.com",
                "username": "ana",
                "phone": "912345678",
                "vat": "123456789",
            },
        )

        user = get_user_model().objects.get(username="ana")
        self.assertRedirects(response, reverse("backoffice:user_detail", args=[user.pk]))
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.customer_profile.billing_phone, "912345678")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("https://www.olhocao.pt/accounts/reset/", mail.outbox[0].body)

    def test_duplicate_email_is_rejected_case_insensitively(self):
        get_user_model().objects.create_user(username="existing", email="ana@example.com")

        response = self.client.post(
            reverse("backoffice:user_create"),
            {
                "first_name": "Ana",
                "last_name": "Silva",
                "email": "ANA@example.com",
                "username": "ana",
                "phone": "912345678",
                "vat": "123456789",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        self.assertEqual(len(mail.outbox), 0)


class AssistedBookingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user(
            username="manager",
            password="test-password-123",
            is_staff=True,
        )
        customer_user = User.objects.create_user(
            username="customer",
            password="test-password-123",
            email="customer@example.com",
        )
        self.customer = CustomerProfile.objects.create(
            user=customer_user,
            billing_phone="912345678",
            tax_number="123456789",
        )
        self.client.force_login(self.staff)

    def test_staff_without_customer_profile_is_sent_to_customer_selector(self):
        response = self.client.get(reverse("hotel:booking_stay"))

        self.assertRedirects(response, reverse("backoffice:users_list"))

    def test_assisted_booking_keeps_staff_identity_and_uses_selected_customer(self):
        response = self.client.post(
            reverse("backoffice:assisted_booking_start", args=[self.customer.user_id])
        )

        self.assertRedirects(response, reverse("hotel:booking_stay"))
        self.assertEqual(self.client.session["assisted_customer_id"], self.customer.pk)
        self.assertEqual(self.client.get(reverse("hotel:booking_stay")).status_code, 200)

    def test_stopping_assisted_booking_clears_customer_and_wizard_state(self):
        session = self.client.session
        session["assisted_customer_id"] = self.customer.pk
        session["booking_data"] = {"stay_id": "1"}
        session.save()

        response = self.client.post(reverse("backoffice:assisted_booking_stop"))

        self.assertRedirects(response, reverse("backoffice:users_list"))
        self.assertNotIn("assisted_customer_id", self.client.session)
        self.assertNotIn("booking_data", self.client.session)

    def test_assisted_activity_keeps_staff_identity_and_selected_customer(self):
        response = self.client.post(
            reverse("backoffice:assisted_booking_start", args=[self.customer.user_id]),
            {"destination": "activities"},
        )

        self.assertRedirects(response, reverse("activities:offering_list"))
        self.assertEqual(self.client.session["assisted_customer_id"], self.customer.pk)


class BookingDashboardFilterTests(TestCase):
    def setUp(self):
        self.customer = create_account()
        self.booking, self.stay = create_booking(
            self.customer,
            status=BookingStatus.PAID,
            start_in_days=5,
            duration=4,
        )

    def test_overlap_filter_includes_stay_that_started_before_period(self):
        results = filtered_booking_stays(
            {
                "start_date": self.stay.start_date + timedelta(days=1),
                "end_date": self.stay.end_date + timedelta(days=1),
                "date_mode": "overlap",
            }
        )

        self.assertEqual(list(results), [self.stay])

    def test_medication_filter_only_returns_priority_dogs(self):
        self.stay.pet.medication = "Daily tablet"
        self.stay.pet.save(update_fields=("medication",))
        other_customer = create_account(username="no-medication")
        create_booking(
            other_customer,
            status=BookingStatus.PAID,
            stay_item=create_catalog_item(remote_id="second-stay"),
        )

        results = filtered_booking_stays({"medical": "medication"})

        self.assertEqual(list(results), [self.stay])


class ActivityOperationsDashboardTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="operations",
            password="test-password-123",
            is_staff=True,
        )
        self.customer = create_account()
        self.pet = create_pet(self.customer)
        offering = create_offering()
        self.entitlement = offering.entitlements.create(
            customer=self.customer,
            pet=self.pet,
            status=EntitlementStatus.ACTIVE,
            item_name_snapshot=offering.catalog_item.name,
            unit_price_eur_snapshot=offering.price_eur,
            merchant_reference="activity-operations",
            current_period_end=timezone.now() + timedelta(days=30),
        )
        CreditLedgerEntry.objects.create(
            entitlement=self.entitlement,
            delta=2,
            reason=LedgerReason.PURCHASE,
            idempotency_key="operations-credits",
        )
        self.reservation = reserve_session(
            entitlement=self.entitlement,
            session=create_session(),
            actor=self.customer.user,
        )
        self.client.force_login(self.staff)

    def test_staff_can_mark_attendance_from_operations_dashboard(self):
        self.reservation.session.starts_at = timezone.now() - timedelta(hours=2)
        self.reservation.session.ends_at = timezone.now() - timedelta(hours=1)
        self.reservation.session.save(update_fields=("starts_at", "ends_at", "updated_at"))
        response = self.client.post(
            reverse(
                "backoffice:activity_reservation_status",
                args=(self.reservation.pk,),
            ),
            {"status": ReservationStatus.ATTENDED},
        )

        self.assertRedirects(response, reverse("backoffice:activities_dashboard"))
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, ReservationStatus.ATTENDED)

    def test_staff_cannot_mark_future_session_as_attended(self):
        response = self.client.post(
            reverse(
                "backoffice:activity_reservation_status",
                args=(self.reservation.pk,),
            ),
            {"status": ReservationStatus.ATTENDED},
        )

        self.assertRedirects(response, reverse("backoffice:activities_dashboard"))
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, ReservationStatus.BOOKED)

    def test_dashboard_uses_a_dedicated_audited_cancellation_page(self):
        response = self.client.get(reverse("backoffice:activities_dashboard"))

        self.assertContains(
            response,
            reverse(
                "backoffice:activity_reservation_cancel",
                args=(self.reservation.pk,),
            ),
        )
        self.assertNotContains(response, 'name="status" value="cancelled"')

    def test_staff_can_return_a_late_use_with_an_explicit_reason(self):
        self.reservation.session.starts_at = timezone.now() + timedelta(hours=1)
        self.reservation.session.ends_at = timezone.now() + timedelta(hours=3)
        self.reservation.session.save(update_fields=("starts_at", "ends_at", "updated_at"))
        self.assertEqual(self.entitlement.credit_balance, 1)

        response = self.client.post(
            reverse(
                "backoffice:activity_reservation_cancel",
                args=(self.reservation.pk,),
            ),
            {
                "reason": "Compensação autorizada pela gerência",
                "restore_entitlement": "on",
            },
        )

        self.assertRedirects(response, reverse("backoffice:activities_dashboard"))
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, ReservationStatus.CANCELLED)
        self.assertTrue(self.reservation.cancellation_policy_overridden)
        self.assertEqual(self.entitlement.credit_balance, 2)

    def test_staff_cancellation_requires_a_reason(self):
        response = self.client.post(
            reverse(
                "backoffice:activity_reservation_cancel",
                args=(self.reservation.pk,),
            ),
            {"reason": "", "restore_entitlement": "on"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required")
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, ReservationStatus.BOOKED)

    def test_status_endpoint_cannot_bypass_cancellation_reason(self):
        response = self.client.post(
            reverse(
                "backoffice:activity_reservation_status",
                args=(self.reservation.pk,),
            ),
            {"status": ReservationStatus.CANCELLED},
        )

        self.assertRedirects(response, reverse("backoffice:activities_dashboard"))
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, ReservationStatus.BOOKED)

    def test_external_return_url_is_rejected(self):
        response = self.client.post(
            reverse(
                "backoffice:activity_reservation_status",
                args=(self.reservation.pk,),
            ),
            {
                "status": ReservationStatus.ATTENDED,
                "next": "https://attacker.example/redirect",
            },
        )

        self.assertRedirects(response, reverse("backoffice:activities_dashboard"))


class BackofficeAuthorizationTests(TestCase):
    def test_customer_cannot_view_legal_document_management(self):
        customer = create_account()
        self.client.force_login(customer.user)

        response = self.client.get(reverse("backoffice:legal_documents"))

        self.assertEqual(response.status_code, 403)

    def test_external_user_toggle_return_url_is_rejected(self):
        staff = get_user_model().objects.create_user(
            username="security-manager",
            password="test-password-123",
            is_staff=True,
        )
        customer = create_account(username="toggle-customer")
        self.client.force_login(staff)

        response = self.client.post(
            reverse("backoffice:user_toggle_active", args=(customer.user_id,)),
            {"next": "https://attacker.example/redirect"},
        )

        self.assertRedirects(
            response,
            reverse("backoffice:user_detail", args=(customer.user_id,)),
        )


class LegalDocumentManagementTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="legal-manager",
            password="test-password-123",
            is_staff=True,
        )
        self.client.force_login(self.staff)

    def test_multiple_new_sections_keep_their_own_clauses(self):
        response = self.client.post(
            reverse("backoffice:legal_document_create"),
            self._document_payload(
                sections=(
                    ("Reservas", "Bookings", "Cláusula de reservas", "Booking clause"),
                    ("Pagamentos", "Payments", "Cláusula de pagamento", "Payment clause"),
                )
            ),
        )

        self.assertRedirects(response, reverse("backoffice:legal_documents"))
        document = LegalDocument.objects.get(name_pt="Termos de teste")
        sections = list(document.sections.prefetch_related("items"))
        self.assertEqual([section.title_pt for section in sections], ["Reservas", "Pagamentos"])
        self.assertEqual(sections[0].items.get().text_pt, "Cláusula de reservas")
        self.assertEqual(sections[1].items.get().text_pt, "Cláusula de pagamento")

    def test_deleted_section_is_ignored_without_reindexing_remaining_forms(self):
        document = LegalDocument.objects.create(
            doc_type=LegalDocument.DocumentType.TERMS,
            name_pt="Termos antigos",
            name_en="Old terms",
            heading_pt="Condições antigas",
            heading_en="Old conditions",
            effective_date="2026-01-01",
        )
        removed_section = LegalDocumentSection.objects.create(
            document=document,
            title_pt="Remover",
            title_en="Remove",
            order=0,
        )
        removed_clause = LegalDocumentSectionLineItem.objects.create(
            section=removed_section,
            text_pt="Texto removido",
            text_en="Removed text",
            order=0,
        )
        retained_section = LegalDocumentSection.objects.create(
            document=document,
            title_pt="Manter",
            title_en="Keep",
            order=1,
        )
        retained_clause = LegalDocumentSectionLineItem.objects.create(
            section=retained_section,
            text_pt="Texto mantido",
            text_en="Retained text",
            order=0,
        )
        payload = self._document_payload(
            sections=(
                ("Remover", "Remove", "", ""),
                ("Manter atualizado", "Keep updated", "Texto atualizado", "Updated text"),
            ),
            initial_sections=2,
        )
        payload.update(
            {
                "sections-0-id": removed_section.pk,
                "sections-0-DELETE": "on",
                "sections-1-id": retained_section.pk,
                "lineitems-0-INITIAL_FORMS": 1,
                "lineitems-0-0-id": removed_clause.pk,
                "lineitems-1-INITIAL_FORMS": 1,
                "lineitems-1-0-id": retained_clause.pk,
            }
        )

        response = self.client.post(
            reverse("backoffice:legal_document_update", args=(document.pk,)),
            payload,
        )

        self.assertRedirects(response, reverse("backoffice:legal_documents"))
        self.assertFalse(LegalDocumentSection.objects.filter(pk=removed_section.pk).exists())
        retained_section.refresh_from_db()
        retained_clause.refresh_from_db()
        self.assertEqual(retained_section.title_pt, "Manter atualizado")
        self.assertEqual(retained_section.order, 0)
        self.assertEqual(retained_clause.text_pt, "Texto atualizado")

    @staticmethod
    def _document_payload(*, sections, initial_sections=0):
        payload = {
            "doc_type": LegalDocument.DocumentType.TERMS,
            "name_pt": "Termos de teste",
            "name_en": "Test terms",
            "heading_pt": "Condições aplicáveis",
            "heading_en": "Applicable conditions",
            "effective_date": "2026-09-02",
            "sections-TOTAL_FORMS": len(sections),
            "sections-INITIAL_FORMS": initial_sections,
            "sections-MIN_NUM_FORMS": 0,
            "sections-MAX_NUM_FORMS": 1000,
        }
        for index, (title_pt, title_en, text_pt, text_en) in enumerate(sections):
            payload.update(
                {
                    f"sections-{index}-title_pt": title_pt,
                    f"sections-{index}-title_en": title_en,
                    f"sections-{index}-order": index,
                    f"lineitems-{index}-TOTAL_FORMS": 1,
                    f"lineitems-{index}-INITIAL_FORMS": 0,
                    f"lineitems-{index}-MIN_NUM_FORMS": 0,
                    f"lineitems-{index}-MAX_NUM_FORMS": 1000,
                    f"lineitems-{index}-0-text_pt": text_pt,
                    f"lineitems-{index}-0-text_en": text_en,
                    f"lineitems-{index}-0-order": 0,
                }
            )
        return payload


class BackofficeCustomerManagementTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="customer-manager",
            password="test-password-123",
            is_staff=True,
        )
        self.customer = create_account(username="managed-customer")
        self.client.force_login(self.staff)

    def test_customer_list_excludes_technical_administrators(self):
        response = self.client.get(reverse("backoffice:users_list"))

        listed_user_ids = {user.pk for user in response.context["users"]}
        self.assertIn(self.customer.user_id, listed_user_ids)
        self.assertNotIn(self.staff.pk, listed_user_ids)

    def test_staff_can_set_customer_password_with_django_validation(self):
        response = self.client.post(
            reverse(
                "backoffice:customer_password_set",
                args=(self.customer.user_id,),
            ),
            {
                "new_password1": "A-new-strong-password-123",
                "new_password2": "A-new-strong-password-123",
            },
        )

        self.assertRedirects(
            response,
            reverse("backoffice:user_detail", args=(self.customer.user_id,)),
        )
        self.customer.user.refresh_from_db()
        self.assertTrue(self.customer.user.check_password("A-new-strong-password-123"))

    def test_customer_cannot_set_another_customers_password(self):
        self.client.force_login(self.customer.user)

        response = self.client.get(
            reverse(
                "backoffice:customer_password_set",
                args=(self.customer.user_id,),
            )
        )

        self.assertEqual(response.status_code, 403)

    def test_pending_booking_action_tells_staff_to_send_payment_link(self):
        create_booking(self.customer, status=BookingStatus.PENDING)

        response = self.client.get(reverse("backoffice:user_detail", args=(self.customer.user_id,)))

        self.assertContains(response, "Send link for payment")
        self.assertNotContains(response, "Complete Payment")

    def test_staff_can_block_and_restore_pack_access_with_an_audit_trail(self):
        offering = create_offering(mode=OfferingMode.PACK)
        entitlement = offering.entitlements.create(
            customer=self.customer,
            pet=create_pet(self.customer),
            status=EntitlementStatus.ACTIVE,
            item_name_snapshot=offering.catalog_item.name,
            unit_price_eur_snapshot=offering.price_eur,
            merchant_reference="managed-pack-access",
            current_period_end=timezone.now() + timedelta(days=30),
        )
        CreditLedgerEntry.objects.create(
            entitlement=entitlement,
            delta=3,
            reason=LedgerReason.PURCHASE,
            idempotency_key="managed-pack-credits",
        )

        response = self.client.post(
            reverse("backoffice:entitlement_access", args=(entitlement.pk,)),
            {"action": "block", "reason": "Disputa comunicada pelo cliente"},
        )

        self.assertRedirects(
            response,
            reverse("backoffice:entitlement_detail", args=(entitlement.pk,)),
        )
        entitlement.refresh_from_db()
        self.assertFalse(entitlement.has_access)
        self.assertEqual(entitlement.credit_balance, 3)
        self.assertTrue(
            SubscriptionManagementEvent.objects.filter(
                entitlement=entitlement,
                action=SubscriptionManagementAction.ACCESS_BLOCKED,
                actor=self.staff,
            ).exists()
        )

        self.client.post(
            reverse("backoffice:entitlement_access", args=(entitlement.pk,)),
            {"action": "restore", "reason": "Situação validada pela gestão"},
        )

        entitlement.refresh_from_db()
        self.assertTrue(entitlement.has_access)
        self.assertTrue(
            SubscriptionManagementEvent.objects.filter(
                entitlement=entitlement,
                action=SubscriptionManagementAction.ACCESS_RESTORED,
                actor=self.staff,
            ).exists()
        )


class DeveloperAdminAccessTests(TestCase):
    def test_staff_operator_cannot_open_django_admin(self):
        operator = get_user_model().objects.create_user(
            username="operator",
            password="test-password-123",
            is_staff=True,
        )
        self.client.force_login(operator)

        response = self.client.get(reverse("admin:index"))

        self.assertRedirects(
            response,
            f"{reverse('admin:login')}?next={reverse('admin:index')}",
        )

    def test_superuser_can_open_django_admin(self):
        developer = get_user_model().objects.create_superuser(
            username="developer",
            email="developer@example.com",
            password="test-password-123",
        )
        self.client.force_login(developer)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)


class ActivitySessionManagementTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="session-manager",
            password="test-password-123",
            is_staff=True,
        )
        self.client.force_login(self.staff)
        self.catalog_type = CatalogType.objects.create(
            name="ATL",
            name_pt="ATL",
            name_en="Daycare",
            slug="atl-session-tests",
            workflow=CatalogWorkflow.FULL_DAY_SLOT,
            default_capacity=12,
        )

    def create_reserved_session(self, *, capacity=12, reservation_count=1):
        offering = create_offering(
            catalog_type=self.catalog_type,
            remote_id="session-management-offer",
        )
        session = create_session(
            catalog_type=self.catalog_type,
            capacity=capacity,
        )
        for index in range(reservation_count):
            customer = create_account(username=f"session-customer-{index}")
            pet = create_pet(customer, name=f"Lua {index}")
            entitlement = offering.entitlements.create(
                customer=customer,
                pet=pet,
                status=EntitlementStatus.ACTIVE,
                item_name_snapshot=offering.catalog_item.name,
                unit_price_eur_snapshot=offering.price_eur,
                merchant_reference=f"session-management-entitlement-{index}",
                current_period_end=timezone.now() + timedelta(days=60),
            )
            CreditLedgerEntry.objects.create(
                entitlement=entitlement,
                delta=1,
                reason=LedgerReason.PURCHASE,
                idempotency_key=f"session-management-credit-{index}",
            )
            reserve_session(entitlement=entitlement, session=session, actor=customer.user)
        return session, offering

    @staticmethod
    def session_update_payload(session, **overrides):
        starts_at = timezone.localtime(session.starts_at).replace(second=0, microsecond=0)
        ends_at = timezone.localtime(session.ends_at).replace(second=0, microsecond=0)
        payload = {
            "title": session.title,
            "catalog_type": session.catalog_type_id,
            "eligible_levels": [],
            "trainers": [],
            "starts_at": starts_at.strftime("%Y-%m-%dT%H:%M"),
            "ends_at": ends_at.strftime("%Y-%m-%dT%H:%M"),
            "capacity": session.capacity,
            "notes": session.notes,
        }
        payload.update(overrides)
        return payload

    def test_schedule_button_uses_dedicated_backoffice_page(self):
        response = self.client.get(reverse("backoffice:activities_dashboard"))

        self.assertContains(response, reverse("backoffice:activity_session_create"))
        self.assertNotContains(response, "/admin/activities/activitysession/add/")

    def test_staff_can_schedule_session_without_django_admin(self):
        starts_at = timezone.now() + timedelta(days=3)
        response = self.client.post(
            reverse("backoffice:activity_session_create"),
            {
                "title": "ATL da manhã",
                "catalog_type": self.catalog_type.pk,
                "starts_at": starts_at.strftime("%Y-%m-%dT%H:%M"),
                "ends_at": (starts_at + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M"),
                "capacity": 12,
                "notes": "",
            },
        )

        self.assertRedirects(response, reverse("backoffice:activities_dashboard"))
        self.assertTrue(
            ActivitySession.objects.filter(
                title="ATL da manhã",
                created_by=self.staff,
            ).exists()
        )

    def test_medical_filter_uses_compact_checkbox_component(self):
        response = self.client.get(reverse("backoffice:activities_dashboard"))

        self.assertContains(response, 'class="ui-checkbox"')
        self.assertNotContains(response, 'name="medical_priority" class="ui-field"')

    def test_session_form_uses_dynamic_catalog_types(self):
        CatalogType.objects.create(
            name="Grooming",
            slug="flexible-session-tests",
            workflow=CatalogWorkflow.FLEXIBLE,
        )
        response = self.client.get(reverse("backoffice:activity_session_create"))

        self.assertContains(response, "ATL")
        self.assertNotContains(response, "Grooming")
        self.assertContains(response, "Session name")
        self.assertContains(response, "Maximum number of dogs")

    def test_overlapping_trainer_requires_an_explicit_audited_override(self):
        timed_type = CatalogType.objects.create(
            name="Treino",
            name_pt="Treino",
            name_en="Training",
            slug="timed-session-conflicts",
            workflow=CatalogWorkflow.TIMED_SLOT,
            default_capacity=8,
        )
        trainer = Trainer.objects.create(name="Ana")
        starts_at = timezone.localtime(timezone.now() + timedelta(days=4)).replace(
            second=0,
            microsecond=0,
        )
        existing = ActivitySession.objects.create(
            title="Treino existente",
            catalog_type=timed_type,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            capacity=8,
        )
        existing.trainers.add(trainer)
        payload = {
            "title": "Treino sobreposto",
            "catalog_type": timed_type.pk,
            "trainers": [trainer.pk],
            "starts_at": starts_at.strftime("%Y-%m-%dT%H:%M"),
            "ends_at": (starts_at + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            "capacity": 8,
            "notes": "",
        }

        self.assertTrue(
            trainers_have_conflict(
                [trainer],
                starts_at,
                starts_at + timedelta(hours=1),
            )
        )
        direct_form = ActivitySessionForm(payload)
        self.assertFalse(direct_form.is_valid(), direct_form.cleaned_data)

        blocked = self.client.post(reverse("backoffice:activity_session_create"), payload)

        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, "overlapping session")
        self.assertFalse(ActivitySession.objects.filter(title="Treino sobreposto").exists())

        payload.update(
            override_operational_rules="on",
            override_reason="Gestão confirmou dois treinadores disponíveis",
        )
        allowed = self.client.post(reverse("backoffice:activity_session_create"), payload)

        self.assertRedirects(allowed, reverse("backoffice:activities_dashboard"))
        created = ActivitySession.objects.get(title="Treino sobreposto")
        event = ActivitySessionEvent.objects.get(session=created)
        self.assertTrue(event.details["operational_override"])
        self.assertIn("dois treinadores", event.details["override_reason"])
        self.assertTrue(event.details["conflicts"])

    def test_manual_closure_requires_the_same_audited_override(self):
        starts_at = timezone.localtime(timezone.now() + timedelta(days=5)).replace(
            second=0,
            microsecond=0,
        )
        closure = OperationalClosure.objects.create(
            name="Férias da equipa",
            starts_at=starts_at - timedelta(hours=1),
            ends_at=starts_at + timedelta(hours=5),
            all_operational_services=True,
            reason="Encerramento planeado",
            customer_message="Encerrado para férias",
        )
        response = self.client.post(
            reverse("backoffice:activity_session_create"),
            {
                "title": "ATL durante fecho",
                "catalog_type": self.catalog_type.pk,
                "starts_at": starts_at.strftime("%Y-%m-%dT%H:%M"),
                "ends_at": (starts_at + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M"),
                "capacity": 12,
                "notes": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, closure.customer_message)
        self.assertFalse(ActivitySession.objects.filter(title="ATL durante fecho").exists())

    def test_staff_cancels_a_session_on_the_dedicated_backoffice_page(self):
        session = ActivitySession.objects.create(
            title="ATL excecional",
            catalog_type=self.catalog_type,
            starts_at=timezone.now() + timedelta(days=3),
            ends_at=timezone.now() + timedelta(days=3, hours=4),
            capacity=12,
        )

        edit_response = self.client.get(
            reverse("backoffice:activity_session_edit", kwargs={"pk": session.pk})
        )

        self.assertContains(
            edit_response,
            reverse("backoffice:activity_session_cancel", kwargs={"pk": session.pk}),
        )
        self.assertNotContains(edit_response, "/admin/activities/activitysession/")

        response = self.client.post(
            reverse("backoffice:activity_session_cancel", kwargs={"pk": session.pk}),
            {"reason": "Equipa indisponível durante este dia"},
        )

        self.assertRedirects(response, reverse("backoffice:calendar"))
        session.refresh_from_db()
        self.assertTrue(session.is_cancelled)
        self.assertTrue(
            ActivitySessionEvent.objects.filter(
                session=session,
                action="cancelled",
                actor=self.staff,
            ).exists()
        )

    def test_session_cancellation_requires_an_audited_reason(self):
        session = ActivitySession.objects.create(
            title="ATL sem motivo",
            catalog_type=self.catalog_type,
            starts_at=timezone.now() + timedelta(days=3),
            ends_at=timezone.now() + timedelta(days=3, hours=4),
            capacity=12,
        )

        response = self.client.post(
            reverse("backoffice:activity_session_cancel", kwargs={"pk": session.pk}),
            {"reason": ""},
        )

        self.assertRedirects(
            response,
            reverse("backoffice:activity_session_edit", kwargs={"pk": session.pk}),
        )
        session.refresh_from_db()
        self.assertFalse(session.is_cancelled)

    def test_capacity_cannot_be_reduced_below_existing_reservations(self):
        session, _offering = self.create_reserved_session(capacity=2, reservation_count=2)

        response = self.client.post(
            reverse("backoffice:activity_session_edit", kwargs={"pk": session.pk}),
            self.session_update_payload(session, capacity=1),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already has 2 reserved places")
        session.refresh_from_db()
        self.assertEqual(session.capacity, 2)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_rescheduling_a_reserved_session_is_audited_and_notifies_customer(self):
        session, _offering = self.create_reserved_session()
        previous_starts_at = session.starts_at
        new_starts_at = timezone.localtime(session.starts_at + timedelta(days=1)).replace(
            second=0,
            microsecond=0,
        )
        new_ends_at = new_starts_at + timedelta(hours=2)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("backoffice:activity_session_edit", kwargs={"pk": session.pk}),
                self.session_update_payload(
                    session,
                    starts_at=new_starts_at.strftime("%Y-%m-%dT%H:%M"),
                    ends_at=new_ends_at.strftime("%Y-%m-%dT%H:%M"),
                ),
            )

        self.assertRedirects(response, reverse("backoffice:calendar"))
        session.refresh_from_db()
        self.assertEqual(session.starts_at, new_starts_at)
        event = ActivitySessionEvent.objects.get(session=session, action="updated")
        self.assertEqual(
            event.details["previous_schedule"]["starts_at"],
            previous_starts_at.isoformat(),
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("rescheduled", mail.outbox[0].subject)
        self.assertIn("remains confirmed", mail.outbox[0].body)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_non_schedule_session_edit_does_not_email_customer(self):
        session, _offering = self.create_reserved_session()

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("backoffice:activity_session_edit", kwargs={"pk": session.pk}),
                self.session_update_payload(session, notes="Nota apenas para a equipa"),
            )

        self.assertRedirects(response, reverse("backoffice:calendar"))
        self.assertEqual(len(mail.outbox), 0)

    def test_access_levels_cannot_exclude_existing_reservations(self):
        session, offering = self.create_reserved_session()
        other_level = offering.catalog_level.__class__.objects.create(
            catalog_type=self.catalog_type,
            name="Premium",
            name_pt="Premium",
            name_en="Premium",
            slug="premium-session-management",
        )

        response = self.client.post(
            reverse("backoffice:activity_session_edit", kwargs={"pk": session.pk}),
            self.session_update_payload(session, eligible_levels=[other_level.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "exclude one or more existing reservations")
        self.assertFalse(session.eligible_levels.exists())

    def test_cancelled_session_cannot_be_edited(self):
        session = create_session(catalog_type=self.catalog_type)
        session.is_cancelled = True
        session.save(update_fields=("is_cancelled", "updated_at"))

        response = self.client.post(
            reverse("backoffice:activity_session_edit", kwargs={"pk": session.pk}),
            self.session_update_payload(session, title="Alteração indevida"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cancelled session cannot be changed")
        session.refresh_from_db()
        self.assertNotEqual(session.title, "Alteração indevida")


class CatalogManagementTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="catalog-manager",
            password="test-password-123",
            is_staff=True,
        )
        self.client.force_login(self.staff)
        self.catalog_type = CatalogType.objects.create(
            name="ATL",
            name_pt="ATL",
            name_en="Daycare",
            slug="atl-catalog-tests",
            workflow=CatalogWorkflow.FULL_DAY_SLOT,
            default_capacity=12,
        )

    def offer_payload(self, **overrides):
        payload = {
            "name_pt": "Passe semanal",
            "name_en": "Weekly pass",
            "description_pt": "Acesso semanal ao ATL.",
            "description_en": "Weekly daycare access.",
            "level": "",
            "image_alt_pt": "",
            "image_alt_en": "",
            "toconline_type": CatalogItemType.SERVICE,
            "unit_price_eur": "79.90",
            "tax_code": "NOR",
            "sale_mode": OfferingMode.PACK,
            "included_sessions": "8",
            "sessions_per_week": "",
            "validity_days": "60",
            "billing_frequency": "",
            "cancellation_notice_hours": "24",
            "is_standalone": "on",
            "is_public": "on",
            "is_active": "on",
            "order": "0",
        }
        payload.update(overrides)
        return payload

    def test_catalog_landing_links_to_dynamic_type_creation(self):
        response = self.client.get(reverse("backoffice:catalog"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ATL")
        self.assertContains(response, reverse("backoffice:catalog_type_create"))

    def test_catalog_type_links_to_central_optional_service_management(self):
        response = self.client.get(
            reverse("backoffice:catalog_type_detail", args=(self.catalog_type.pk,))
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Levels, prices and plans")
        self.assertContains(response, "Optional services")
        self.assertContains(
            response,
            f"{reverse('backoffice:catalog_optional_services')}?target=type:{self.catalog_type.pk}",
        )
        self.assertContains(response, "Add price or plan")
        self.assertNotContains(response, "Add offer")

    def test_staff_reorders_every_level_in_one_atomic_request(self):
        first_level = CatalogLevel.objects.create(
            catalog_type=self.catalog_type,
            name="Normal",
            slug="normal-order-test",
            order=0,
        )
        second_level = CatalogLevel.objects.create(
            catalog_type=self.catalog_type,
            name="Premium",
            slug="premium-order-test",
            order=1,
        )

        response = self.client.post(
            reverse("backoffice:catalog_level_order", args=(self.catalog_type.pk,)),
            data=json.dumps({"level_ids": [second_level.pk, first_level.pk]}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(self.catalog_type.levels.order_by("order").values_list("pk", flat=True)),
            [second_level.pk, first_level.pk],
        )
        self.assertEqual(
            list(self.catalog_type.levels.order_by("order").values_list("order", flat=True)),
            [0, 1],
        )

    def test_partial_level_order_is_rejected_without_changes(self):
        first_level = CatalogLevel.objects.create(
            catalog_type=self.catalog_type,
            name="Normal",
            slug="normal-invalid-order-test",
            order=0,
        )
        second_level = CatalogLevel.objects.create(
            catalog_type=self.catalog_type,
            name="Premium",
            slug="premium-invalid-order-test",
            order=1,
        )

        response = self.client.post(
            reverse("backoffice:catalog_level_order", args=(self.catalog_type.pk,)),
            data=json.dumps({"level_ids": [second_level.pk]}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            list(self.catalog_type.levels.order_by("order").values_list("pk", flat=True)),
            [first_level.pk, second_level.pk],
        )

    def test_staff_associates_existing_service_from_central_optional_service_page(self):
        flexible_type = CatalogType.objects.create(
            name="Grooming",
            name_pt="Grooming",
            name_en="Grooming",
            slug="grooming-catalog-tests",
            workflow=CatalogWorkflow.FLEXIBLE,
        )
        grooming = create_offering(
            mode=OfferingMode.ONE_OFF,
            catalog_type=flexible_type,
            level_name="",
            remote_id="grooming-catalog-extra",
        )

        response = self.client.post(
            reverse("backoffice:catalog_optional_services"),
            {
                "target": f"type:{self.catalog_type.pk}",
                "add_on_ids": [grooming.catalog_offer_id],
            },
        )

        expected_url = (
            f"{reverse('backoffice:catalog_optional_services')}"
            f"?target=type%3A{self.catalog_type.pk}"
        )
        self.assertRedirects(response, expected_url)
        association = AddOnAvailability.objects.get(
            target_type=self.catalog_type,
            add_on=grooming.catalog_offer,
        )
        self.assertIsNone(association.target_level)
        self.assertTrue(association.is_active)

    def test_central_optional_service_page_replaces_direct_selection_without_deleting_history(self):
        flexible_type = CatalogType.objects.create(
            name="Grooming",
            slug="grooming-replacement-tests",
            workflow=CatalogWorkflow.FLEXIBLE,
        )
        first = create_offering(
            mode=OfferingMode.ONE_OFF,
            catalog_type=flexible_type,
            level_name="",
            remote_id="grooming-replacement-first",
        )
        second = create_offering(
            mode=OfferingMode.ONE_OFF,
            catalog_type=flexible_type,
            level_name="",
            remote_id="grooming-replacement-second",
        )
        old_association = AddOnAvailability.objects.create(
            target_type=self.catalog_type,
            add_on=first.catalog_offer,
            created_by=self.staff,
        )

        response = self.client.post(
            reverse("backoffice:catalog_optional_services"),
            {
                "target": f"type:{self.catalog_type.pk}",
                "add_on_ids": [second.catalog_offer_id],
            },
        )

        self.assertEqual(response.status_code, 302)
        old_association.refresh_from_db()
        self.assertFalse(old_association.is_active)
        self.assertTrue(
            AddOnAvailability.objects.get(
                target_type=self.catalog_type,
                add_on=second.catalog_offer,
            ).is_active
        )
        self.assertEqual(AddOnAvailability.objects.count(), 2)

    def test_level_page_shows_inherited_optional_service_as_read_only(self):
        level = CatalogLevel.objects.create(
            catalog_type=self.catalog_type,
            name="Premium",
            slug="premium-inherited-extra",
        )
        flexible_type = CatalogType.objects.create(
            name="Grooming",
            slug="grooming-inherited-tests",
            workflow=CatalogWorkflow.FLEXIBLE,
        )
        grooming = create_offering(
            mode=OfferingMode.ONE_OFF,
            catalog_type=flexible_type,
            level_name="",
            remote_id="grooming-inherited-extra",
        )
        AddOnAvailability.objects.create(
            target_type=self.catalog_type,
            add_on=grooming.catalog_offer,
            created_by=self.staff,
        )

        response = self.client.get(
            reverse("backoffice:catalog_optional_services"),
            {"target": f"level:{level.pk}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Inherited from the whole type")
        self.assertNotContains(
            response,
            f'name="add_on_ids" value="{grooming.catalog_offer_id}"',
        )

    def test_catalog_offer_cannot_be_an_optional_service_for_its_own_type(self):
        flexible_type = CatalogType.objects.create(
            name="Grooming",
            slug="grooming-self-reference-tests",
            workflow=CatalogWorkflow.FLEXIBLE,
        )
        grooming = create_offering(
            mode=OfferingMode.ONE_OFF,
            catalog_type=flexible_type,
            level_name="",
            remote_id="grooming-self-reference",
        )

        response = self.client.post(
            reverse("backoffice:catalog_optional_services"),
            {
                "target": f"type:{flexible_type.pk}",
                "add_on_ids": [grooming.catalog_offer_id],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "selection is no longer valid")
        self.assertFalse(AddOnAvailability.objects.exists())

    def test_optional_service_model_rejects_its_own_catalog_type(self):
        flexible_type = CatalogType.objects.create(
            name="Grooming",
            slug="grooming-model-self-reference-tests",
            workflow=CatalogWorkflow.FLEXIBLE,
        )
        grooming = create_offering(
            mode=OfferingMode.ONE_OFF,
            catalog_type=flexible_type,
            level_name="",
            remote_id="grooming-model-self-reference",
        )
        association = AddOnAvailability(
            target_type=flexible_type,
            add_on=grooming.catalog_offer,
        )

        with self.assertRaises(ValidationError):
            association.full_clean()

    def test_staff_creates_sellable_offer_without_calling_toconline(self):
        with patch("backoffice.views.TocOnlineCatalogSynchronizer") as synchronizer:
            response = self.client.post(
                reverse("backoffice:catalog_offer_create", args=(self.catalog_type.pk,)),
                self.offer_payload(),
            )

        offer = CatalogOffer.objects.get(name="Passe semanal")
        self.assertRedirects(
            response,
            reverse("backoffice:catalog_type_detail", args=(self.catalog_type.pk,)),
        )
        self.assertEqual(offer.catalog_item.origin, CatalogItemOrigin.WEBSITE)
        self.assertEqual(offer.catalog_item.sync_status, CatalogSyncStatus.PENDING)
        self.assertTrue(offer.catalog_item.item_code.startswith("OC-S-"))
        self.assertEqual(offer.activity_offering.included_sessions, 8)
        synchronizer.assert_not_called()

    def test_scheduled_offer_must_be_a_fiscal_service(self):
        form = CatalogOfferForm(
            self.offer_payload(toconline_type=CatalogItemType.PRODUCT),
            catalog_type=self.catalog_type,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("toconline_type", form.errors)

    def test_commercial_rename_does_not_change_operational_behavior(self):
        self.catalog_type.name = "Clube de dia Gold"
        self.catalog_type.save(update_fields=("name", "updated_at"))

        self.catalog_type.refresh_from_db()

        self.assertEqual(self.catalog_type.workflow, CatalogWorkflow.FULL_DAY_SLOT)
        self.assertTrue(self.catalog_type.is_scheduled)

    def test_published_price_change_creates_a_new_fiscal_version(self):
        create_form = CatalogOfferForm(
            self.offer_payload(),
            catalog_type=self.catalog_type,
        )
        self.assertTrue(create_form.is_valid(), create_form.errors)
        offer = create_form.save()
        original_item = offer.catalog_item
        original_item.toconline_id = "erp-published-offer"
        original_item.sync_status = CatalogSyncStatus.SYNCED
        original_item.save(update_fields=("toconline_id", "sync_status", "updated_at"))

        update_form = CatalogOfferForm(
            self.offer_payload(unit_price_eur="89.90"),
            catalog_type=self.catalog_type,
            instance=offer,
        )
        self.assertTrue(update_form.is_valid(), update_form.errors)
        updated_offer = update_form.save()

        original_item.refresh_from_db()
        self.assertEqual(updated_offer.catalog_item.supersedes, original_item)
        self.assertEqual(updated_offer.catalog_item.unit_price_eur, Decimal("89.90"))
        self.assertFalse(original_item.is_active)

    @patch("backoffice.views.TocOnlineCatalogSynchronizer")
    def test_catalog_can_be_synchronized_from_dedicated_page(self, synchronizer):
        synchronizer.return_value.synchronize.return_value.created = 1
        synchronizer.return_value.synchronize.return_value.updated = 2
        synchronizer.return_value.synchronize.return_value.deactivated = 0
        synchronizer.return_value.synchronize.return_value.published = 1
        synchronizer.return_value.synchronize.return_value.diverged = 0

        response = self.client.post(reverse("backoffice:catalog_sync"))

        self.assertRedirects(response, reverse("backoffice:catalog"))
        synchronizer.return_value.synchronize.assert_called_once_with()

    def test_customer_cannot_manage_catalog(self):
        customer = create_account(username="catalog-customer")
        self.client.force_login(customer.user)

        response = self.client.get(reverse("backoffice:catalog"))
        reorder_response = self.client.post(
            reverse("backoffice:catalog_level_order", args=(self.catalog_type.pk,)),
            data=json.dumps({"level_ids": []}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(reorder_response.status_code, 403)


class SiteContentManagementTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="content-manager",
            password="test-password-123",
            is_staff=True,
        )
        self.client.force_login(self.staff)

    def site_settings_payload(self):
        form = SiteSettingsForm(instance=SiteSettings.current())
        return {field_name: form[field_name].value() or "" for field_name in form.fields}

    def home_slide_payload(self, slide):
        form = HomeSlideForm(instance=slide)
        return {
            field_name: form[field_name].value()
            for field_name in form.fields
            if field_name != "image" and form[field_name].value() not in (None, False)
        }

    def test_customer_cannot_manage_any_website_content_endpoint(self):
        customer = create_account(username="content-customer")
        self.client.force_login(customer.user)
        routes = (
            reverse("backoffice:site_content"),
            reverse("backoffice:site_settings"),
            reverse("backoffice:site_content_collection", args=("slides",)),
            reverse("backoffice:site_content_create", args=("services",)),
            reverse(
                "backoffice:site_content_edit",
                args=("slides", HomeSlide.objects.first().pk),
            ),
        )

        for route in routes:
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 403)

    def test_staff_can_reach_every_allowlisted_content_collection(self):
        overview = self.client.get(reverse("backoffice:site_content"))

        self.assertEqual(overview.status_code, 200)
        for collection in (
            "slides",
            "services",
            "testimonials",
            "team",
            "partners",
            "social-links",
            "contact-subjects",
        ):
            with self.subTest(collection=collection):
                response = self.client.get(
                    reverse("backoffice:site_content_collection", args=(collection,))
                )
                self.assertEqual(response.status_code, 200)

    def test_staff_updates_shared_settings_and_records_the_editor(self):
        payload = self.site_settings_payload()
        payload.update(
            {
                "public_email": "conteudo@example.com",
                "pricing_title_pt": "Preçário atualizado",
                "pricing_title_en": "Updated pricing",
            }
        )

        response = self.client.post(reverse("backoffice:site_settings"), payload)

        self.assertRedirects(response, reverse("backoffice:site_content"))
        site_settings = SiteSettings.current()
        self.assertEqual(site_settings.public_email, "conteudo@example.com")
        self.assertEqual(site_settings.pricing_title_pt, "Preçário atualizado")
        self.assertEqual(site_settings.updated_by, self.staff)

    def test_staff_creates_bilingual_service_card_without_changing_templates(self):
        response = self.client.post(
            reverse("backoffice:site_content_create", args=("services",)),
            {
                "icon": ServiceHighlight.Icon.DAYCARE,
                "title_pt": "Passeios acompanhados",
                "title_en": "Supervised walks",
                "description_pt": "Passeios adequados a cada cão.",
                "description_en": "Walks adapted to each dog.",
                "features_pt": "Percurso acompanhado\nRitmo adaptado",
                "features_en": "Supervised route\nAdapted pace",
                "action_text_pt": "Contactar",
                "action_text_en": "Contact us",
                "destination": "contact",
                "order": 50,
                "is_active": "on",
            },
        )

        self.assertRedirects(
            response,
            reverse("backoffice:site_content_collection", args=("services",)),
        )
        service = ServiceHighlight.objects.get(title_en="Supervised walks")
        self.assertEqual(service.updated_by, self.staff)
        self.assertContains(self.client.get(reverse("frontoffice:home")), "Supervised walks")

    def test_last_main_slide_cannot_be_hidden(self):
        hero = HomeSlide.objects.filter(kind=HomeSlideKind.HERO, is_active=True).first()
        HomeSlide.objects.filter(kind=HomeSlideKind.HERO).exclude(pk=hero.pk).update(
            is_active=False
        )
        payload = self.home_slide_payload(hero)
        payload.pop("is_active", None)

        response = self.client.post(
            reverse("backoffice:site_content_edit", args=("slides", hero.pk)),
            payload,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Publish at least one main carousel slide")
        hero.refresh_from_db()
        self.assertTrue(hero.is_active)

    def test_map_embed_rejects_non_google_hosts(self):
        payload = self.site_settings_payload()
        payload["maps_embed_url"] = "https://attacker.example/embed"

        response = self.client.post(reverse("backoffice:site_settings"), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Use an HTTPS Google Maps embed URL")

    def test_public_partner_and_social_links_require_https(self):
        partner = PartnerForm(
            {
                "name": "Unsafe partner",
                "category_pt": "Parceiro",
                "category_en": "Partner",
                "website_url": "http://example.com",
                "symbol": "",
                "tagline_pt": "",
                "tagline_en": "",
                "order": 0,
                "is_active": True,
            }
        )
        social = SocialLinkForm(
            {
                "platform": "instagram",
                "url": "http://example.com/profile",
                "order": 0,
                "is_active": True,
            }
        )

        self.assertFalse(partner.is_valid())
        self.assertIn("website_url", partner.errors)
        self.assertFalse(social.is_valid())
        self.assertIn("url", social.errors)

    @override_settings(FRONTOFFICE_IMAGE_MAX_UPLOAD_BYTES=10)
    def test_oversized_website_image_is_rejected(self):
        one_pixel_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
            "AScY42YAAAAASUVORK5CYII="
        )
        form = HomeSlideForm(
            data={
                "kind": HomeSlideKind.BENEFIT,
                "title_pt": "Título",
                "title_en": "Title",
                "description_pt": "Descrição",
                "description_en": "Description",
                "image_alt_pt": "Descrição da imagem",
                "image_alt_en": "Image description",
                "cta_text_pt": "",
                "cta_text_en": "",
                "cta_destination": "",
                "order": 0,
                "is_active": True,
            },
            files={
                "image": SimpleUploadedFile(
                    "large.png",
                    one_pixel_png,
                    content_type="image/png",
                )
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn("image", form.errors)
        self.assertIn("too large", str(form.errors["image"]))
