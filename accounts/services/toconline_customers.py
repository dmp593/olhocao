from django.db import transaction
from django.utils import timezone

from accounts.models import CustomerProfile, CustomerSyncStatus
from olhocao.integrations.toconline import toconline_gateway


def synchronize_customer(profile, *, client=toconline_gateway):
    """Idempotently project one local customer into TOConline."""
    attempted_at = timezone.now()

    try:
        with transaction.atomic():
            locked = (
                CustomerProfile.objects.select_for_update()
                .select_related("user")
                .get(pk=profile.pk)
            )
            if (
                locked.toconline_sync_status == CustomerSyncStatus.SYNCED
                and locked.toconline_customer_id
            ):
                return locked

            locked.toconline_sync_attempted_at = attempted_at
            locked.save(update_fields=("toconline_sync_attempted_at",))
            attributes = _customer_attributes(locked)
            remote_id = locked.toconline_customer_id
            if remote_id:
                client.update_customer(remote_id, **attributes)
            else:
                remote = _find_existing_customer(locked, client=client)
                if remote:
                    remote_id = str(remote["id"])
                    client.update_customer(remote_id, **attributes)
                else:
                    remote = client.create_customer(
                        **attributes,
                        internal_observations="Created by olhocao.pt",
                    )
                    remote_id = str(remote["id"])

            locked.toconline_customer_id = remote_id
            locked.toconline_sync_status = CustomerSyncStatus.SYNCED
            locked.toconline_sync_error = ""
            locked.toconline_sync_attempted_at = attempted_at
            locked.toconline_synced_at = timezone.now()
            locked.save(
                update_fields=(
                    "toconline_customer_id",
                    "toconline_sync_status",
                    "toconline_sync_error",
                    "toconline_sync_attempted_at",
                    "toconline_synced_at",
                )
            )
    except Exception as exc:
        CustomerProfile.objects.filter(pk=profile.pk).update(
            toconline_sync_status=CustomerSyncStatus.FAILED,
            toconline_sync_error=f"{type(exc).__name__}: {str(exc)[:500]}",
            toconline_sync_attempted_at=attempted_at,
        )
        raise
    return locked


def _find_existing_customer(profile, *, client):
    filters = (
        {"tax_registration_number": profile.tax_number},
        {"email": profile.user.email},
        {"mobile_number": profile.billing_phone},
        {"phone_number": profile.billing_phone},
    )
    for customer_filter in filters:
        if not next(iter(customer_filter.values())):
            continue
        customer = client.find_customer(**customer_filter)
        if customer:
            return customer
    return None


def _customer_attributes(profile):
    name = profile.user.get_full_name() or profile.user.username
    return {
        "business_name": name,
        "contact_name": name,
        "email": profile.user.email,
        "mobile_number": profile.billing_phone,
        "tax_registration_number": profile.tax_number,
    }
