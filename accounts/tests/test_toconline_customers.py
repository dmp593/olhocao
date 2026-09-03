from django.test import TestCase

from accounts.models import CustomerSyncStatus
from accounts.services.toconline_customers import synchronize_customer
from hotel.tests.factories import create_account


class FakeCustomerClient:
    def __init__(self, *, fail=False, existing=None):
        self.fail = fail
        self.existing = existing
        self.created = []
        self.updated = []

    def find_customer(self, **filters):
        if self.fail:
            raise RuntimeError("TOConline unavailable")
        return self.existing

    def create_customer(self, **attributes):
        self.created.append(attributes)
        return {"id": "customer-remote-1"}

    def update_customer(self, customer_id, **attributes):
        self.updated.append((customer_id, attributes))


class CustomerSynchronizationTests(TestCase):
    def test_pending_customer_is_created_from_local_billing_data(self):
        profile = create_account()
        client = FakeCustomerClient()

        synchronized = synchronize_customer(profile, client=client)

        self.assertEqual(synchronized.toconline_customer_id, "customer-remote-1")
        self.assertEqual(synchronized.toconline_sync_status, CustomerSyncStatus.SYNCED)
        self.assertEqual(client.created[0]["tax_registration_number"], "123456789")

    def test_failure_remains_retryable_without_changing_local_customer(self):
        profile = create_account()

        with self.assertRaises(RuntimeError):
            synchronize_customer(profile, client=FakeCustomerClient(fail=True))

        profile.refresh_from_db()
        self.assertEqual(profile.toconline_sync_status, CustomerSyncStatus.FAILED)
        self.assertIn("RuntimeError", profile.toconline_sync_error)
        self.assertEqual(profile.billing_phone, "912345678")

    def test_existing_remote_customer_is_reused_idempotently(self):
        profile = create_account()
        client = FakeCustomerClient(existing={"id": "existing-remote"})

        synchronized = synchronize_customer(profile, client=client)

        self.assertEqual(synchronized.toconline_customer_id, "existing-remote")
        self.assertEqual(client.created, [])
        self.assertEqual(client.updated[0][0], "existing-remote")

    def test_already_synchronized_customer_is_not_sent_twice(self):
        profile = create_account()
        profile.toconline_customer_id = "existing-remote"
        profile.toconline_sync_status = CustomerSyncStatus.SYNCED
        profile.save(
            update_fields=(
                "toconline_customer_id",
                "toconline_sync_status",
            )
        )
        client = FakeCustomerClient()

        synchronized = synchronize_customer(profile, client=client)

        self.assertEqual(synchronized.toconline_customer_id, "existing-remote")
        self.assertEqual(client.created, [])
        self.assertEqual(client.updated, [])
