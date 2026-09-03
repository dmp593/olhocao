from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.test import TestCase
from django.utils import timezone

from activities.models import (
    ActivityPaymentEvent,
    CreditLedgerEntry,
    EntitlementStatus,
    OfferingMode,
    RefundStatus,
    SubscriptionCycle,
    SubscriptionCycleStatus,
    SubscriptionManagementAction,
)
from activities.services.billing import (
    add_billing_period,
    create_entitlement,
    process_activity_notification,
    send_entitlement_payment_link_email,
)
from activities.tests.factories import create_offering
from hotel.services.payments import PaymentGatewayError, PaymentLinkResult, SubscriptionResult
from hotel.tests.factories import create_account, create_pet


class FakePurchaseClient:
    def create_single_payment_link(self, **kwargs):
        self.single_payload = kwargs
        return PaymentLinkResult(
            provider_link_id="link-1",
            url="https://pay.example/pack",
            qr_code_url="https://pay.example/pack/qr",
            expires_at=kwargs["expires_at"],
        )

    def create_subscription(self, **kwargs):
        self.subscription_payload = kwargs
        return SubscriptionResult("subscription-1", "https://pay.example/card")


class FakePackNotificationClient:
    def __init__(self, entitlement):
        self.entitlement = entitlement

    def get_payment(self, provider_id):
        return {
            "id": provider_id,
            "key": self.entitlement.merchant_reference,
            "value": "49.90",
            "currency": "EUR",
            "payment_status": "paid",
            "method": {"type": "MBW"},
            "captures": [{"id": "capture-1", "status": "success"}],
        }


class FailedPackNotificationClient(FakePackNotificationClient):
    def get_payment(self, provider_id):
        payment = super().get_payment(provider_id)
        payment["payment_status"] = "failed"
        payment["captures"] = []
        return payment


class FakeSubscriptionNotificationClient:
    def __init__(self, entitlement):
        self.entitlement = entitlement
        self.refund_requests = []

    def get_subscription(self, provider_id):
        return {
            "message": {
                "id": provider_id,
                "key": self.entitlement.merchant_reference,
                "value": 49.90,
                "method": {"type": "CC", "status": "active"},
                "transactions": [
                    {
                        "id": "subscription-transaction-1",
                        "status": "success",
                        "capture": {"id": "subscription-capture-1", "status": "success"},
                    }
                ],
            }
        }

    def create_refund(self, capture_id, *, amount, reference):
        self.refund_requests.append((capture_id, amount, reference))
        return {"id": "subscription-refund-1"}


class FailingRefundClient(FakeSubscriptionNotificationClient):
    def create_refund(self, capture_id, *, amount, reference):
        raise PaymentGatewayError("Temporary refund failure")


class UnconfirmedSubscriptionCaptureClient(FakeSubscriptionNotificationClient):
    def get_subscription(self, provider_id):
        response = super().get_subscription(provider_id)
        response["message"]["transactions"] = [{"id": "failed-transaction", "status": "failed"}]
        return response


class WrongSubscriptionCurrencyClient(FakeSubscriptionNotificationClient):
    def get_subscription(self, provider_id):
        response = super().get_subscription(provider_id)
        response["message"]["currency"] = "USD"
        return response


class FailedCycleAfterSuccessfulCycleClient(FakeSubscriptionNotificationClient):
    def get_subscription(self, provider_id):
        response = super().get_subscription(provider_id)
        response["message"]["transactions"].append(
            {
                "id": "subscription-transaction-2",
                "status": "failed",
            }
        )
        return response


class FakeChargebackClient:
    def __init__(self, *, capture_id, payment_id=""):
        self.capture_id = capture_id
        self.payment_id = payment_id
        self.stopped = []

    def list_chargebacks(self, capture_id):
        return [
            {
                "id": "chargeback-1",
                "amount": "49.90",
                "capture": {
                    "id": self.capture_id,
                    "payment_id": self.payment_id,
                },
            }
        ]

    def stop_subscription(self, provider_subscription_id):
        self.stopped.append(provider_subscription_id)


class ActivityBillingTests(TestCase):
    def setUp(self):
        self.customer = create_account()
        self.pet = create_pet(self.customer)

    def test_pack_checkout_uses_toconline_price_snapshot(self):
        offering = create_offering()
        client = FakePurchaseClient()

        entitlement = create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=offering,
            actor=self.customer.user,
            client=client,
        )

        self.assertEqual(entitlement.unit_price_eur_snapshot, Decimal("49.90"))
        self.assertEqual(entitlement.payment_url, "https://pay.example/pack")
        self.assertEqual(client.single_payload["amount"], Decimal("49.90"))
        self.assertEqual(client.single_payload["customer"]["phone"], "+351912345678")

    def test_subscription_checkout_returns_card_authorisation_url(self):
        offering = create_offering(mode=OfferingMode.SUBSCRIPTION)
        client = FakePurchaseClient()

        entitlement = create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=offering,
            actor=self.customer.user,
            client=client,
        )

        self.assertEqual(entitlement.provider_subscription_id, "subscription-1")
        self.assertEqual(entitlement.payment_url, "https://pay.example/card")
        self.assertTrue(client.subscription_payload["next_charge_at"] > timezone.now())

    def test_staff_delivery_emails_the_checkout_link_to_the_customer(self):
        offering = create_offering()
        entitlement = create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=offering,
            actor=self.customer.user,
            client=FakePurchaseClient(),
            notify_customer=False,
        )

        send_entitlement_payment_link_email(entitlement)

        self.assertEqual(mail.outbox[0].to, ["customer@example.com"])
        self.assertIn(entitlement.payment_url, mail.outbox[0].body)

    def test_expired_pack_link_cannot_be_emailed(self):
        offering = create_offering()
        entitlement = create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=offering,
            actor=self.customer.user,
            client=FakePurchaseClient(),
            notify_customer=False,
        )
        entitlement.payment_expires_at = timezone.now() - timedelta(minutes=1)
        entitlement.save(update_fields=("payment_expires_at", "updated_at"))

        with self.assertRaisesMessage(PaymentGatewayError, "no longer available"):
            send_entitlement_payment_link_email(entitlement)

        self.assertEqual(len(mail.outbox), 0)

    @patch("activities.services.billing._send_activation_email")
    @patch("activities.services.billing.issue_activity_document_safely")
    def test_pack_capture_is_idempotent_and_credits_once(self, issue_document, send_email):
        offering = create_offering()
        entitlement = create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=offering,
            actor=self.customer.user,
            client=FakePurchaseClient(),
        )
        payload = {
            "id": "payment-1",
            "key": entitlement.merchant_reference,
            "type": "capture",
            "status": "success",
            "date": "2026-09-02 01:00:00",
        }
        client = FakePackNotificationClient(entitlement)

        process_activity_notification(payload, client=client)
        process_activity_notification(payload, client=client)

        entitlement.refresh_from_db()
        self.assertEqual(entitlement.status, EntitlementStatus.ACTIVE)
        self.assertEqual(entitlement.credit_balance, 5)
        self.assertEqual(CreditLedgerEntry.objects.count(), 1)
        self.assertEqual(ActivityPaymentEvent.objects.count(), 1)
        self.assertEqual(issue_document.call_count, 2)
        self.assertEqual(send_email.call_count, 2)

    def test_failed_pack_capture_is_recorded_without_granting_credits(self):
        offering = create_offering()
        entitlement = create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=offering,
            actor=self.customer.user,
            client=FakePurchaseClient(),
        )

        result = process_activity_notification(
            {
                "id": "failed-pack-payment",
                "key": entitlement.merchant_reference,
                "type": "capture",
                "status": "failed",
                "date": "2026-09-02 01:00:00",
            },
            client=FailedPackNotificationClient(entitlement),
        )

        result.refresh_from_db()
        self.assertEqual(result.status, EntitlementStatus.PENDING)
        self.assertEqual(result.credit_balance, 0)
        self.assertIn("safely try again", result.last_payment_error)
        self.assertEqual(ActivityPaymentEvent.objects.filter(provider_status="failed").count(), 1)

    def test_stale_failed_pack_notification_does_not_replace_an_active_state(self):
        offering = create_offering()
        entitlement = offering.entitlements.create(
            customer=self.customer,
            pet=self.pet,
            status=EntitlementStatus.ACTIVE,
            item_name_snapshot=offering.catalog_item.name,
            unit_price_eur_snapshot=offering.price_eur,
            merchant_reference="active-pack-with-stale-failure",
            last_payment_error="",
        )

        process_activity_notification(
            {
                "id": "failed-old-pack-payment",
                "key": entitlement.merchant_reference,
                "type": "capture",
                "status": "failed",
            },
            client=FailedPackNotificationClient(entitlement),
        )

        entitlement.refresh_from_db()
        self.assertEqual(entitlement.status, EntitlementStatus.ACTIVE)
        self.assertEqual(entitlement.last_payment_error, "")

    def test_late_authorisation_does_not_clear_a_suspended_subscription_alert(self):
        offering = create_offering(mode=OfferingMode.SUBSCRIPTION)
        entitlement = create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=offering,
            actor=self.customer.user,
            client=FakePurchaseClient(),
        )
        entitlement.status = EntitlementStatus.SUSPENDED
        entitlement.access_blocked_at = timezone.now()
        entitlement.last_payment_error = "Financial review required."
        entitlement.save(
            update_fields=(
                "status",
                "access_blocked_at",
                "last_payment_error",
                "updated_at",
            )
        )

        process_activity_notification(
            {
                "id": "subscription-1",
                "key": entitlement.merchant_reference,
                "type": "subscription_create",
                "status": "success",
            },
            client=FakeSubscriptionNotificationClient(entitlement),
        )

        entitlement.refresh_from_db()
        self.assertEqual(entitlement.status, EntitlementStatus.SUSPENDED)
        self.assertEqual(entitlement.last_payment_error, "Financial review required.")

    def test_pack_chargeback_blocks_access_without_deleting_credits_or_receipt(self):
        offering = create_offering()
        entitlement = offering.entitlements.create(
            customer=self.customer,
            pet=self.pet,
            status=EntitlementStatus.ACTIVE,
            item_name_snapshot=offering.catalog_item.name,
            unit_price_eur_snapshot=offering.price_eur,
            merchant_reference="pack-chargeback",
            provider_payment_id="single-pack-1",
            provider_capture_id="pack-capture-1",
            toconline_sale_document_id="receipt-pack-1",
        )
        CreditLedgerEntry.objects.create(
            entitlement=entitlement,
            delta=5,
            reason="purchase",
            idempotency_key="pack-chargeback-purchase",
        )

        result = process_activity_notification(
            {
                "id": "single-pack-1",
                "key": entitlement.merchant_reference,
                "type": "chargeback",
                "status": "success",
            },
            client=FakeChargebackClient(
                capture_id="pack-capture-1",
                payment_id="single-pack-1",
            ),
        )

        result.refresh_from_db()
        self.assertEqual(result.status, EntitlementStatus.ACTIVE)
        self.assertFalse(result.has_access)
        self.assertEqual(result.credit_balance, 5)
        self.assertEqual(result.toconline_sale_document_id, "receipt-pack-1")
        self.assertEqual(
            result.management_events.get().action,
            SubscriptionManagementAction.PAYMENT_DISPUTED,
        )

    def test_failed_subscription_authorisation_remains_pending_with_safe_retry_message(self):
        offering = create_offering(mode=OfferingMode.SUBSCRIPTION)
        entitlement = create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=offering,
            actor=self.customer.user,
            client=FakePurchaseClient(),
        )

        result = process_activity_notification(
            {
                "id": "subscription-1",
                "key": entitlement.merchant_reference,
                "type": "subscription_create",
                "status": "failed",
            },
            client=FakeSubscriptionNotificationClient(entitlement),
        )

        result.refresh_from_db()
        self.assertEqual(result.status, EntitlementStatus.PENDING)
        self.assertEqual(result.payment_method, "")
        self.assertIn("could not be authorised", result.last_payment_error)
        self.assertFalse(SubscriptionCycle.objects.exists())

    def test_failed_cycle_does_not_reuse_an_earlier_successful_capture_id(self):
        offering = create_offering(mode=OfferingMode.SUBSCRIPTION)
        entitlement = create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=offering,
            actor=self.customer.user,
            client=FakePurchaseClient(),
        )

        process_activity_notification(
            {
                "id": "subscription-1",
                "key": entitlement.merchant_reference,
                "type": "subscription_capture",
                "status": "failed",
            },
            client=FailedCycleAfterSuccessfulCycleClient(entitlement),
        )

        entitlement.refresh_from_db()
        cycle = SubscriptionCycle.objects.get()
        self.assertEqual(entitlement.status, EntitlementStatus.PAST_DUE)
        self.assertEqual(cycle.provider_cycle_id, "transaction:subscription-transaction-2")
        self.assertEqual(cycle.provider_capture_id, "")

    def test_failed_renewal_preserves_access_until_paid_period_ends(self):
        offering = create_offering(mode=OfferingMode.SUBSCRIPTION)
        entitlement = create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=offering,
            actor=self.customer.user,
            client=FakePurchaseClient(),
        )
        entitlement.status = EntitlementStatus.ACTIVE
        entitlement.current_period_end = timezone.now() + timedelta(days=5)
        entitlement.save(update_fields=("status", "current_period_end", "updated_at"))

        process_activity_notification(
            {
                "id": "subscription-1",
                "key": entitlement.merchant_reference,
                "type": "subscription_capture",
                "status": "failed",
            },
            client=FailedCycleAfterSuccessfulCycleClient(entitlement),
        )

        entitlement.refresh_from_db()
        self.assertEqual(entitlement.status, EntitlementStatus.ACTIVE)
        self.assertTrue(entitlement.has_access)
        self.assertIn("could not collect", entitlement.last_payment_error)

    @patch("activities.services.billing._send_activation_email")
    @patch("activities.services.billing.issue_activity_document_safely")
    def test_successful_subscription_cycle_activates_access(self, issue_document, send_email):
        offering = create_offering(mode=OfferingMode.SUBSCRIPTION)
        entitlement = create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=offering,
            actor=self.customer.user,
            client=FakePurchaseClient(),
        )
        payload = {
            "id": "subscription-1",
            "key": entitlement.merchant_reference,
            "type": "subscription_capture",
            "status": "success",
            "date": "2026-09-02 01:00:00",
        }

        process_activity_notification(
            payload,
            client=FakeSubscriptionNotificationClient(entitlement),
        )

        entitlement.refresh_from_db()
        self.assertEqual(entitlement.status, EntitlementStatus.ACTIVE)
        self.assertTrue(entitlement.current_period_end > timezone.now())
        self.assertEqual(SubscriptionCycle.objects.count(), 1)
        self.assertEqual(
            SubscriptionCycle.objects.get().provider_cycle_id,
            "capture:subscription-capture-1",
        )
        issue_document.assert_called_once()

    def test_subscription_chargeback_suspends_future_billing_and_blocks_access(self):
        offering = create_offering(mode=OfferingMode.SUBSCRIPTION)
        entitlement = offering.entitlements.create(
            customer=self.customer,
            pet=self.pet,
            status=EntitlementStatus.ACTIVE,
            item_name_snapshot=offering.catalog_item.name,
            unit_price_eur_snapshot=offering.price_eur,
            merchant_reference="subscription-chargeback",
            provider_subscription_id="subscription-1",
            current_period_end=timezone.now() + timedelta(days=20),
        )
        cycle = SubscriptionCycle.objects.create(
            entitlement=entitlement,
            provider_cycle_id="capture:subscription-capture-1",
            provider_capture_id="subscription-capture-1",
            status=SubscriptionCycleStatus.PAID,
            amount_eur=Decimal("49.90"),
            toconline_sale_document_id="receipt-cycle-1",
        )
        client = FakeChargebackClient(capture_id="subscription-capture-1")
        payload = {
            "id": "subscription-capture-1",
            "key": entitlement.merchant_reference,
            "type": "chargeback",
            "status": "success",
        }

        process_activity_notification(payload, client=client)
        process_activity_notification(payload, client=client)

        entitlement.refresh_from_db()
        cycle.refresh_from_db()
        self.assertEqual(entitlement.status, EntitlementStatus.SUSPENDED)
        self.assertFalse(entitlement.has_access)
        self.assertEqual(cycle.status, SubscriptionCycleStatus.DISPUTED)
        self.assertEqual(cycle.toconline_sale_document_id, "receipt-cycle-1")
        self.assertEqual(client.stopped, ["subscription-1"])
        self.assertEqual(ActivityPaymentEvent.objects.filter(event_type="chargeback").count(), 1)

    @patch("activities.services.billing._send_activation_email")
    @patch("activities.services.billing.issue_activity_document_safely")
    def test_subscription_redelivery_with_different_timestamp_does_not_create_a_new_cycle(
        self,
        issue_document,
        send_email,
    ):
        offering = create_offering(mode=OfferingMode.SUBSCRIPTION)
        entitlement = create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=offering,
            actor=self.customer.user,
            client=FakePurchaseClient(),
        )
        payload = {
            "id": "subscription-1",
            "key": entitlement.merchant_reference,
            "type": "subscription_capture",
            "status": "success",
            "date": "2026-09-02 01:00:00",
        }
        client = FakeSubscriptionNotificationClient(entitlement)

        process_activity_notification(payload, client=client)
        process_activity_notification(
            {**payload, "date": "2026-09-02 01:00:05"},
            client=client,
        )

        self.assertEqual(SubscriptionCycle.objects.count(), 1)
        self.assertEqual(ActivityPaymentEvent.objects.count(), 2)
        self.assertEqual(issue_document.call_count, 2)
        self.assertEqual(send_email.call_count, 2)

    @patch("activities.services.billing._send_unexpected_subscription_payment_email")
    @patch("activities.services.billing.issue_activity_document_safely")
    def test_capture_after_subscription_closure_is_refunded_without_restoring_access(
        self,
        issue_document,
        send_notice,
    ):
        offering = create_offering(mode=OfferingMode.SUBSCRIPTION)
        entitlement = create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=offering,
            actor=self.customer.user,
            client=FakePurchaseClient(),
        )
        entitlement.status = EntitlementStatus.CANCELLED
        entitlement.save(update_fields=("status", "updated_at"))
        payload = {
            "id": "subscription-1",
            "key": entitlement.merchant_reference,
            "type": "subscription_capture",
            "status": "success",
            "date": "2026-09-02 01:00:00",
        }
        client = FakeSubscriptionNotificationClient(entitlement)

        process_activity_notification(payload, client=client)

        entitlement.refresh_from_db()
        cycle = SubscriptionCycle.objects.get()
        self.assertEqual(entitlement.status, EntitlementStatus.CANCELLED)
        self.assertEqual(cycle.refund_status, "pending")
        self.assertEqual(client.refund_requests[0][0], "subscription-capture-1")
        issue_document.assert_called_once()
        send_notice.assert_called_once()

    @patch("activities.services.billing.issue_activity_document_safely", return_value=None)
    def test_failed_automatic_refund_email_promises_a_retry_instead_of_success(
        self,
        issue_document,
    ):
        offering = create_offering(mode=OfferingMode.SUBSCRIPTION)
        entitlement = create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=offering,
            actor=self.customer.user,
            client=FakePurchaseClient(),
        )
        entitlement.status = EntitlementStatus.CANCELLED
        entitlement.save(update_fields=("status", "updated_at"))

        process_activity_notification(
            {
                "id": "subscription-1",
                "key": entitlement.merchant_reference,
                "type": "subscription_capture",
                "status": "success",
                "date": "2026-09-02 01:00:00",
            },
            client=FailingRefundClient(entitlement),
        )

        cycle = SubscriptionCycle.objects.get()
        self.assertEqual(cycle.refund_status, RefundStatus.FAILED)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("our team will retry the refund", mail.outbox[0].body)

    def test_unpaid_past_due_subscription_does_not_grant_access(self):
        offering = create_offering(mode=OfferingMode.SUBSCRIPTION)
        entitlement = offering.entitlements.create(
            customer=self.customer,
            pet=self.pet,
            status=EntitlementStatus.PAST_DUE,
            item_name_snapshot=offering.catalog_item.name,
            unit_price_eur_snapshot=offering.price_eur,
            merchant_reference="activity-unpaid-subscription",
        )

        self.assertFalse(entitlement.has_access)

    def test_success_notification_requires_a_confirmed_provider_capture(self):
        offering = create_offering(mode=OfferingMode.SUBSCRIPTION)
        entitlement = create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=offering,
            actor=self.customer.user,
            client=FakePurchaseClient(),
        )

        with self.assertRaisesMessage(PaymentGatewayError, "no successful subscription capture"):
            process_activity_notification(
                {
                    "id": "subscription-1",
                    "key": entitlement.merchant_reference,
                    "type": "subscription_capture",
                    "status": "success",
                },
                client=UnconfirmedSubscriptionCaptureClient(entitlement),
            )

        entitlement.refresh_from_db()
        self.assertEqual(entitlement.status, EntitlementStatus.PENDING)
        self.assertFalse(SubscriptionCycle.objects.exists())

    def test_subscription_currency_is_verified_against_the_purchase_snapshot(self):
        offering = create_offering(mode=OfferingMode.SUBSCRIPTION)
        entitlement = create_entitlement(
            customer=self.customer,
            pet=self.pet,
            offering=offering,
            actor=self.customer.user,
            client=FakePurchaseClient(),
        )

        with self.assertRaisesMessage(PaymentGatewayError, "currency does not match"):
            process_activity_notification(
                {
                    "id": "subscription-1",
                    "key": entitlement.merchant_reference,
                    "type": "subscription_capture",
                    "status": "success",
                },
                client=WrongSubscriptionCurrencyClient(entitlement),
            )

        self.assertFalse(SubscriptionCycle.objects.exists())

    def test_calendar_month_period_does_not_drift(self):
        value = timezone.now().replace(year=2028, month=1, day=31)
        result = add_billing_period(value, "1M")
        self.assertEqual((result.year, result.month, result.day), (2028, 2, 29))
