import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import uuid4

import requests
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from hotel.models import (
    Booking,
    BookingDocumentStatus,
    BookingEvent,
    BookingEventType,
    BookingStatus,
    PaymentAttempt,
    PaymentEvent,
    PaymentStatus,
)

logger = logging.getLogger(__name__)


class PaymentGatewayError(RuntimeError):
    """A safe, user-displayable payment gateway error."""


class PaymentGatewayUnavailable(PaymentGatewayError):
    """A retryable provider or network failure."""


@dataclass(frozen=True)
class PaymentLinkResult:
    provider_link_id: str
    url: str
    qr_code_url: str
    expires_at: datetime


@dataclass(frozen=True)
class SubscriptionResult:
    provider_subscription_id: str
    authorisation_url: str


@dataclass(frozen=True)
class PaymentCompletion:
    booking: Booking
    outcome: str


PAYMENT_CONFIRMED = "confirmed"
PAYMENT_AFTER_CANCELLATION = "after_cancellation"
PAYMENT_AFTER_PRICE_CHANGE = "after_price_change"
PAYMENT_AFTER_SERVICE_START = "after_service_start"
DUPLICATE_PAYMENT = "duplicate"
MINIMUM_ONLINE_PAYMENT_EUR = Decimal("0.50")


class EasypayClient:
    def __init__(self, session=None):
        self.session = session or requests.Session()
        environment = settings.EASYPAY_ENVIRONMENT.lower()
        if environment not in {"sandbox", "production"}:
            raise PaymentGatewayError(_("Easypay environment is invalid."))
        self.base_url = (
            "https://api.prod.easypay.pt/2.0"
            if environment == "production"
            else "https://api.test.easypay.pt/2.0"
        )

    def create_payment_link(
        self,
        payment_attempt,
        *,
        customer,
        expires_at,
        notify_customer=True,
    ):
        return self.create_single_payment_link(
            amount=payment_attempt.amount_eur,
            merchant_reference=payment_attempt.merchant_reference,
            description=f"Olhocao booking {payment_attempt.booking_id}",
            customer=customer,
            expires_at=expires_at,
            notify_customer=notify_customer,
        )

    def create_single_payment_link(
        self,
        *,
        amount,
        merchant_reference,
        description,
        customer,
        expires_at,
        notify_customer=True,
    ):
        payload = {
            "type": "SINGLE",
            "expiration_time": expires_at.isoformat().replace("+00:00", "Z"),
            "payment": {
                "single": {
                    "requested_amount": format(amount, ".2f"),
                },
                "methods": settings.EASYPAY_PAYMENT_METHODS,
                "capture": {
                    "descriptive": description[:255],
                    "key": merchant_reference,
                },
            },
            "customer": customer,
        }
        if notify_customer:
            payload["communication_channels"] = ["EMAIL"]

        data = self._request(
            "POST",
            "/link",
            json=payload,
            idempotency_key=merchant_reference,
        )
        provider_link_id = str(data.get("id") or "")
        url = str(data.get("url") or "")
        if not provider_link_id or not url:
            raise PaymentGatewayError(_("Easypay created an incomplete payment link response."))

        return PaymentLinkResult(
            provider_link_id=provider_link_id,
            url=url,
            qr_code_url=str(data.get("image") or ""),
            expires_at=expires_at,
        )

    def create_subscription(
        self,
        *,
        amount,
        merchant_reference,
        description,
        frequency,
        customer,
        next_charge_at,
    ):
        payload = {
            "value": float(amount),
            "currency": "EUR",
            "method": "CC",
            "key": merchant_reference,
            "frequency": frequency,
            "start_time": next_charge_at.strftime("%Y-%m-%d %H:%M"),
            "unlimited_payments": True,
            "capture_now": True,
            "retries": 2,
            "failover": False,
            "customer": customer,
            "capture": {
                "descriptive": description[:255],
                "transaction_key": merchant_reference,
            },
        }
        response = self._request(
            "POST",
            "/subscription",
            json=payload,
            idempotency_key=merchant_reference,
        )
        data = response.get("message", response) if isinstance(response, dict) else {}
        provider_id = str(data.get("id") or "")
        authorisation_url = str((data.get("method") or {}).get("url") or "")
        if not provider_id or not authorisation_url:
            raise PaymentGatewayError(_("Easypay returned an incomplete subscription response."))
        return SubscriptionResult(provider_id, authorisation_url)

    def get_subscription(self, provider_subscription_id):
        return self._request("GET", f"/subscription/{provider_subscription_id}")

    def stop_subscription(self, provider_subscription_id):
        return self._request(
            "PATCH",
            f"/subscription/{provider_subscription_id}",
            json={"status": "inactive"},
            idempotency_key=_provider_idempotency_key(
                "stop-subscription",
                provider_subscription_id,
            ),
        )

    def resume_subscription(self, provider_subscription_id, *, frequency, start_at):
        return self._request(
            "PATCH",
            f"/subscription/{provider_subscription_id}",
            json={
                "status": "active",
                "frequency": frequency,
                "start_time": start_at.strftime("%Y-%m-%d %H:%M"),
            },
            idempotency_key=_provider_idempotency_key(
                "resume-subscription",
                provider_subscription_id,
                start_at.isoformat(),
            ),
        )

    def get_payment_link(self, provider_link_id):
        return self._request("GET", f"/link/{provider_link_id}")

    def get_payment(self, provider_payment_id):
        return self._request("GET", f"/single/{provider_payment_id}")

    def find_payment_by_key(self, merchant_reference):
        response = self._request(
            "GET",
            "/single",
            params={"key": merchant_reference},
        )
        payments = response.get("data", []) if isinstance(response, dict) else []
        return payments[0] if payments else None

    def cancel_payment_link(self, provider_link_id):
        self._request(
            "DELETE",
            f"/link/{provider_link_id}",
            expect_json=False,
            allow_not_found=True,
        )

    def create_refund(self, provider_capture_id, *, amount, reference):
        return self._request(
            "POST",
            f"/refund/{provider_capture_id}",
            json={
                "value": float(amount),
                "transaction_key": reference,
            },
            idempotency_key=reference,
        )

    def get_refund(self, provider_refund_id):
        return self._request("GET", f"/refund/{provider_refund_id}")

    def list_chargebacks(self, provider_capture_id):
        response = self._request(
            "GET",
            "/chargeback",
            params={"capture.id": provider_capture_id, "limit": 100},
        )
        chargebacks = response.get("data", []) if isinstance(response, dict) else []
        if not isinstance(chargebacks, list):
            raise PaymentGatewayUnavailable(
                _("The payment service returned an invalid chargeback response.")
            )
        return chargebacks

    def _request(
        self,
        method,
        path,
        *,
        json=None,
        params=None,
        expect_json=True,
        idempotency_key=None,
        allow_not_found=False,
    ):
        if not settings.EASYPAY_ACCOUNT_ID or not settings.EASYPAY_API_KEY:
            raise PaymentGatewayError(_("Easypay credentials are not configured."))

        headers = {
            "AccountId": settings.EASYPAY_ACCOUNT_ID,
            "ApiKey": settings.EASYPAY_API_KEY,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if method.upper() in {"POST", "PATCH"}:
            if not idempotency_key:
                raise PaymentGatewayError(_("A payment operation is missing its idempotency key."))
            headers["Idempotency-Key"] = _provider_idempotency_key(idempotency_key)

        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                json=json,
                params=params,
                timeout=settings.EASYPAY_TIMEOUT,
            )
            if allow_not_found and response.status_code == 404:
                return {}
            response.raise_for_status()
        except requests.RequestException as exc:
            status_code = getattr(exc.response, "status_code", None)
            logger.exception(
                "Easypay request failed",
                extra={"method": method, "path": path, "status_code": status_code},
            )
            raise PaymentGatewayUnavailable(
                _("The payment service is temporarily unavailable.")
            ) from exc

        if not expect_json or response.status_code == 204:
            return {}

        try:
            return response.json()
        except ValueError as exc:
            raise PaymentGatewayUnavailable(
                _("The payment service returned an invalid response.")
            ) from exc


def normalize_portuguese_phone(phone):
    compact = "".join(character for character in str(phone or "") if character.isdigit())
    if compact.startswith("00351"):
        compact = compact[5:]
    elif compact.startswith("351") and len(compact) > 9:
        compact = compact[3:]

    if len(compact) != 9:
        raise PaymentGatewayError(
            _("A valid Portuguese phone number is required to create the payment link.")
        )
    return f"+351{compact}"


def create_payment_attempt(booking, *, client=None, notify_customer=True):
    client = client or EasypayClient()
    booking = Booking.objects.select_related("customer__user").get(pk=booking.pk)
    customer_profile = booking.customer
    user = booking.customer.user
    if not user.email:
        raise PaymentGatewayError(_("An email address is required to create the payment link."))

    customer = {
        "name": user.get_full_name() or user.username,
        "email": user.email,
        "phone": normalize_portuguese_phone(customer_profile.phone_number),
        "language": "PT",
    }
    expires_at = timezone.now() + timedelta(hours=settings.EASYPAY_PAYMENT_LINK_TTL_HOURS)

    with transaction.atomic():
        locked_booking = Booking.objects.select_for_update().get(pk=booking.pk)
        paid_attempt = locked_booking.payment_attempts.filter(status=PaymentStatus.PAID).first()
        if paid_attempt:
            return paid_attempt
        if locked_booking.status != BookingStatus.PENDING:
            raise PaymentGatewayError(_("This booking cannot be paid in its current state."))
        if (
            locked_booking.earliest_start_date is not None
            and locked_booking.earliest_start_date < timezone.localdate()
        ):
            raise PaymentGatewayError(
                _("This booking can no longer be paid because its start date passed.")
            )

        existing_attempt = locked_booking.payment_attempts.filter(
            status__in=(PaymentStatus.PENDING, PaymentStatus.FAILED),
        ).first()
        if existing_attempt:
            if existing_attempt.can_be_paid:
                return existing_attempt
            if existing_attempt.expires_at and existing_attempt.expires_at <= timezone.now():
                existing_attempt.status = PaymentStatus.EXPIRED
                existing_attempt.save(update_fields=("status", "updated_at"))
                existing_attempt = None

        if existing_attempt is None:
            amount = locked_booking.total_price_eur
            if amount < MINIMUM_ONLINE_PAYMENT_EUR:
                raise PaymentGatewayError(
                    _("The booking total is below the minimum online payment amount.")
                )
            payment_attempt = PaymentAttempt.objects.create(
                booking=locked_booking,
                merchant_reference=f"booking-{booking.pk}-{uuid4().hex[:16]}",
                amount_eur=amount,
                expires_at=expires_at,
            )
        else:
            payment_attempt = existing_attempt
            expires_at = payment_attempt.expires_at or expires_at

    try:
        result = client.create_payment_link(
            payment_attempt,
            customer=customer,
            expires_at=expires_at,
            notify_customer=notify_customer,
        )
    except PaymentGatewayError as exc:
        payment_attempt.status = PaymentStatus.FAILED
        payment_attempt.last_error = str(exc)
        payment_attempt.save(update_fields=("status", "last_error", "updated_at"))
        raise

    payment_attempt.provider_link_id = result.provider_link_id
    payment_attempt.payment_url = result.url
    payment_attempt.qr_code_url = result.qr_code_url
    payment_attempt.expires_at = result.expires_at
    payment_attempt.status = PaymentStatus.PENDING
    payment_attempt.last_error = ""
    payment_attempt.save(
        update_fields=(
            "provider_link_id",
            "payment_url",
            "qr_code_url",
            "expires_at",
            "status",
            "last_error",
            "updated_at",
        )
    )
    return payment_attempt


def send_booking_payment_link_email(payment_attempt):
    """Resend an existing hosted link without exposing it to backoffice staff."""
    payment_attempt = PaymentAttempt.objects.select_related("booking__customer__user").get(
        pk=payment_attempt.pk
    )
    if not payment_attempt.can_be_paid:
        raise PaymentGatewayError(_("This payment link is no longer available."))

    booking = payment_attempt.booking
    user = booking.customer.user
    if not user.email:
        raise PaymentGatewayError(_("An email address is required to send the payment link."))
    try:
        send_mail(
            _("Complete payment for booking #%(booking_id)s") % {"booking_id": booking.pk},
            (
                _(
                    "Hello %(name)s,\n\n"
                    "Use this secure link to pay for booking #%(booking_id)s:\n"
                    "%(payment_url)s\n\n"
                    "If you did not request this booking, contact Olhó Cão."
                )
                % {
                    "name": user.get_full_name() or user.username,
                    "booking_id": booking.pk,
                    "payment_url": payment_attempt.payment_url,
                }
            ),
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            using="default",
        )
    except Exception as exc:
        logger.exception(
            "Could not resend booking payment link",
            extra={"booking_id": booking.pk, "payment_attempt_id": payment_attempt.pk},
        )
        raise PaymentGatewayError(
            _("The payment link could not be emailed. Please try again.")
        ) from exc


def process_payment_notification(payload, *, client=None):
    client = client or EasypayClient()
    reference = str(payload.get("key") or "")
    if not reference:
        raise PaymentGatewayError("Payment notification has no merchant reference.")

    transaction_data = payload.get("transaction") or {}
    event_type = str(payload.get("type") or transaction_data.get("type") or "capture").lower()
    if event_type == "refund":
        from hotel.models import BookingRefund

        booking_refund = (
            BookingRefund.objects.select_related("payment_attempt__booking")
            .filter(refund_reference=reference)
            .first()
        )
        if booking_refund is not None:
            return _process_booking_refund_notification(
                booking_refund,
                payload,
                client=client,
            )
    reference_field = "refund_reference" if event_type == "refund" else "merchant_reference"
    payment_attempt = (
        PaymentAttempt.objects.select_related("booking")
        .filter(**{reference_field: reference})
        .first()
    )
    if payment_attempt is None:
        raise PaymentGatewayError("Unknown payment notification reference.")

    fingerprint = _notification_fingerprint(payload)
    if PaymentEvent.objects.filter(fingerprint=fingerprint).exists():
        if event_type == "chargeback":
            return payment_attempt
        if event_type == "refund":
            issue_confirmed_booking_credit_note(payment_attempt)
            return payment_attempt
        _complete_payment_side_effects(
            PaymentCompletion(
                booking=payment_attempt.booking,
                outcome=_existing_payment_outcome(payment_attempt),
            ),
            payment_attempt,
            client=client,
        )
        return payment_attempt

    if event_type == "refund":
        confirmed_attempt = _process_refund_notification(
            payment_attempt,
            payload,
            fingerprint=fingerprint,
            client=client,
        )
        issue_confirmed_booking_credit_note(confirmed_attempt)
        return confirmed_attempt

    if event_type == "chargeback":
        return _process_chargeback_notification(
            payment_attempt,
            payload,
            fingerprint=fingerprint,
            client=client,
        )

    if event_type != "capture":
        raise PaymentGatewayError("Unsupported Easypay notification type.")
    provider_payment_id = str(payload.get("id") or "")
    if not provider_payment_id:
        raise PaymentGatewayError("Payment notification has no provider id.")
    payment = client.get_payment(provider_payment_id)
    if not _provider_payment_is_paid(payment):
        if str(payload.get("status") or "").lower() in {
            "failed",
            "failure",
            "declined",
            "cancelled",
            "error",
        }:
            _verify_payment_identity(payment_attempt, payment)
            return _record_failed_payment_notification(
                payment_attempt,
                payload,
                fingerprint=fingerprint,
            )
        raise PaymentGatewayError("Easypay has not confirmed this payment.")
    verified = _verified_paid_payment(
        payment_attempt,
        payment,
        notified_payload=payload,
    )

    with transaction.atomic():
        locked_attempt = (
            PaymentAttempt.objects.select_for_update()
            .select_related("booking")
            .get(pk=payment_attempt.pk)
        )
        PaymentEvent.objects.get_or_create(
            fingerprint=fingerprint,
            defaults={
                "payment_attempt": locked_attempt,
                "event_type": event_type,
                "provider_event_id": str(payload.get("id") or ""),
                "provider_status": str(payload.get("status") or ""),
            },
        )
        completion = _mark_payment_paid(
            locked_attempt,
            provider_payment_id=provider_payment_id,
            provider_capture_id=verified["capture_id"],
            provider_transaction_id=str(transaction_data.get("id") or ""),
            method=verified["method"],
        )

    _complete_payment_side_effects(completion, locked_attempt, client=client)
    return locked_attempt


def _process_booking_refund_notification(booking_refund, payload, *, client):
    """Handle a refund against a valid sale, independently of capture corrections."""

    from hotel.models import BookingRefundStatus
    from hotel.services.refunds import confirm_booking_refund

    provider_refund_id = str(payload.get("id") or "")
    if not provider_refund_id:
        raise PaymentGatewayError("Refund notification has no provider id.")

    fingerprint = _notification_fingerprint(payload)
    confirmed = confirm_booking_refund(
        booking_refund,
        provider_refund_id=provider_refund_id,
        client=client,
    )
    if confirmed.status in {
        BookingRefundStatus.REFUNDED,
        BookingRefundStatus.FAILED,
    }:
        PaymentEvent.objects.get_or_create(
            fingerprint=fingerprint,
            defaults={
                "payment_attempt": confirmed.payment_attempt,
                "event_type": "booking_refund",
                "provider_event_id": provider_refund_id,
                "provider_status": str(payload.get("status") or ""),
                "details": {"booking_refund_id": confirmed.pk},
            },
        )
    return confirmed.payment_attempt


def refresh_payment_attempt(payment_attempt, *, client=None):
    client = client or EasypayClient()
    if not payment_attempt.provider_link_id:
        return payment_attempt

    payment = client.find_payment_by_key(payment_attempt.merchant_reference)
    if payment and str(payment.get("payment_status") or "").lower() == "paid":
        provider_payment_id = str(payment.get("id") or "")
        payment = client.get_payment(provider_payment_id)
        verified = _verified_paid_payment(payment_attempt, payment)
        with transaction.atomic():
            locked_attempt = (
                PaymentAttempt.objects.select_for_update()
                .select_related("booking")
                .get(pk=payment_attempt.pk)
            )
            fingerprint = hashlib.sha256(
                f"reconciliation:{provider_payment_id}:paid".encode()
            ).hexdigest()
            PaymentEvent.objects.get_or_create(
                fingerprint=fingerprint,
                defaults={
                    "payment_attempt": locked_attempt,
                    "event_type": "reconciliation",
                    "provider_event_id": provider_payment_id,
                    "provider_status": "paid",
                },
            )
            completion = _mark_payment_paid(
                locked_attempt,
                provider_payment_id=provider_payment_id,
                provider_capture_id=verified["capture_id"],
                provider_transaction_id=verified["transaction_id"],
                method=verified["method"],
            )
        _complete_payment_side_effects(completion, locked_attempt, client=client)
        return locked_attempt

    link = client.get_payment_link(payment_attempt.provider_link_id)
    provider_status = str(link.get("status") or "").upper()
    mapped_status = {
        "EXPIRED": PaymentStatus.EXPIRED,
        "DISABLED": PaymentStatus.CANCELLED,
    }.get(provider_status)
    if mapped_status and payment_attempt.status == PaymentStatus.PENDING:
        payment_attempt.status = mapped_status
        payment_attempt.save(update_fields=("status", "updated_at"))
    return payment_attempt


def request_refund(payment_attempt, amount, *, client=None):
    client = client or EasypayClient()
    amount = _money(amount)

    with transaction.atomic():
        payment_attempt = PaymentAttempt.objects.select_for_update().get(pk=payment_attempt.pk)
        if amount is None or not Decimal("0.00") < amount <= payment_attempt.amount_eur:
            raise PaymentGatewayError("The refund amount is invalid.")
        if not payment_attempt.provider_capture_id:
            raise PaymentGatewayError(
                "The provider capture id is unavailable; reconcile the payment first."
            )
        if (
            payment_attempt.refund_amount_eur is not None
            and payment_attempt.refund_amount_eur != amount
        ):
            raise PaymentGatewayError("A refund retry must use the originally requested amount.")
        if payment_attempt.status == PaymentStatus.REFUNDED:
            return payment_attempt
        if (
            payment_attempt.status == PaymentStatus.REFUND_PENDING
            and payment_attempt.provider_refund_id
        ):
            return payment_attempt
        if payment_attempt.status == PaymentStatus.DISPUTED:
            raise PaymentGatewayError(
                _("A disputed payment requires financial review before any refund action.")
            )
        if payment_attempt.status not in {
            PaymentStatus.PAID,
            PaymentStatus.REFUND_PENDING,
            PaymentStatus.REFUND_FAILED,
        }:
            raise PaymentGatewayError(_("This payment cannot be refunded in its current state."))
        reference = payment_attempt.refund_reference or (
            f"refund-{payment_attempt.pk}-{uuid4().hex[:12]}"
        )
        payment_attempt.status = PaymentStatus.REFUND_PENDING
        payment_attempt.refund_reference = reference
        payment_attempt.refund_amount_eur = amount
        payment_attempt.last_error = ""
        payment_attempt.save(
            update_fields=(
                "status",
                "refund_reference",
                "refund_amount_eur",
                "last_error",
                "updated_at",
            )
        )

    try:
        response = client.create_refund(
            payment_attempt.provider_capture_id,
            amount=amount,
            reference=reference,
        )
    except PaymentGatewayError as exc:
        payment_attempt.status = PaymentStatus.REFUND_FAILED
        payment_attempt.last_error = str(exc)
        payment_attempt.save(update_fields=("status", "last_error", "updated_at"))
        raise

    payment_attempt.provider_refund_id = str(response.get("id") or "")
    if not payment_attempt.provider_refund_id:
        payment_attempt.status = PaymentStatus.REFUND_FAILED
        payment_attempt.last_error = "Easypay returned an incomplete refund response."
        payment_attempt.save(
            update_fields=("provider_refund_id", "status", "last_error", "updated_at")
        )
        raise PaymentGatewayError("Easypay returned an incomplete refund response.")
    payment_attempt.save(update_fields=("provider_refund_id", "updated_at"))
    return payment_attempt


def _process_refund_notification(
    payment_attempt,
    payload,
    *,
    fingerprint,
    client,
):
    provider_refund_id = str(payload.get("id") or "")
    if not provider_refund_id:
        raise PaymentGatewayError("Refund notification has no provider id.")
    refund = client.get_refund(provider_refund_id)
    if not _provider_refund_is_confirmed(payment_attempt, refund):
        payment_attempt.refresh_from_db(fields=("status", "last_error"))
        if payment_attempt.status == PaymentStatus.REFUND_FAILED:
            PaymentEvent.objects.get_or_create(
                fingerprint=fingerprint,
                defaults={
                    "payment_attempt": payment_attempt,
                    "event_type": "refund",
                    "provider_event_id": provider_refund_id,
                    "provider_status": str(refund.get("status") or "failed"),
                },
            )
        return payment_attempt

    return _mark_refund_confirmed(
        payment_attempt,
        provider_refund_id=provider_refund_id,
        fingerprint=fingerprint,
        provider_status=str(payload.get("status") or ""),
    )


def reconcile_pending_refund(payment_attempt, *, client=None):
    """Recover a confirmed provider refund when its webhook was not delivered."""
    client = client or EasypayClient()
    payment_attempt = PaymentAttempt.objects.get(pk=payment_attempt.pk)
    if payment_attempt.status != PaymentStatus.REFUND_PENDING:
        return payment_attempt
    if not payment_attempt.provider_refund_id:
        raise PaymentGatewayError("The provider refund id is unavailable.")

    refund = client.get_refund(payment_attempt.provider_refund_id)
    if not _provider_refund_is_confirmed(payment_attempt, refund):
        return payment_attempt

    fingerprint = hashlib.sha256(
        f"refund-reconciliation:{payment_attempt.provider_refund_id}:success".encode()
    ).hexdigest()
    confirmed_attempt = _mark_refund_confirmed(
        payment_attempt,
        provider_refund_id=payment_attempt.provider_refund_id,
        fingerprint=fingerprint,
        provider_status="success",
    )
    issue_confirmed_booking_credit_note(confirmed_attempt)
    return confirmed_attempt


def _provider_refund_is_confirmed(payment_attempt, refund):
    provider_status = str(refund.get("status") or "").lower()
    if str(refund.get("transaction_key") or "") != payment_attempt.refund_reference:
        raise PaymentGatewayError("The refund reference does not match.")
    if _money(refund.get("value")) != payment_attempt.refund_amount_eur:
        raise PaymentGatewayError("The refund amount does not match.")
    if provider_status in ("pending", "waiting"):
        return False
    if provider_status != "success":
        PaymentAttempt.objects.filter(pk=payment_attempt.pk).update(
            status=PaymentStatus.REFUND_FAILED,
            last_error="Easypay reported a failed refund.",
        )
        return False
    return True


def _mark_refund_confirmed(
    payment_attempt,
    *,
    provider_refund_id,
    fingerprint,
    provider_status,
):
    with transaction.atomic():
        locked_attempt = PaymentAttempt.objects.select_for_update().get(pk=payment_attempt.pk)
        PaymentEvent.objects.get_or_create(
            fingerprint=fingerprint,
            defaults={
                "payment_attempt": locked_attempt,
                "event_type": "refund",
                "provider_event_id": provider_refund_id,
                "provider_status": provider_status,
            },
        )
        locked_attempt.status = PaymentStatus.REFUNDED
        locked_attempt.provider_refund_id = provider_refund_id or locked_attempt.provider_refund_id
        locked_attempt.refunded_at = locked_attempt.refunded_at or timezone.now()
        locked_attempt.last_error = ""
        locked_attempt.save(
            update_fields=(
                "status",
                "provider_refund_id",
                "refunded_at",
                "last_error",
                "updated_at",
            )
        )
    return locked_attempt


def _mark_payment_paid(
    payment_attempt,
    *,
    method="",
    provider_payment_id="",
    provider_capture_id="",
    provider_transaction_id="",
):
    now = timezone.now()
    attempt_status_before_payment = payment_attempt.status
    payment_already_in_refund = payment_attempt.status in {
        PaymentStatus.REFUND_PENDING,
        PaymentStatus.REFUND_FAILED,
        PaymentStatus.REFUNDED,
        PaymentStatus.DISPUTED,
    }
    if not payment_already_in_refund:
        payment_attempt.status = PaymentStatus.PAID
    payment_attempt.payment_method = method or payment_attempt.payment_method
    payment_attempt.provider_payment_id = provider_payment_id or payment_attempt.provider_payment_id
    payment_attempt.provider_capture_id = provider_capture_id or payment_attempt.provider_capture_id
    payment_attempt.provider_transaction_id = (
        provider_transaction_id or payment_attempt.provider_transaction_id
    )
    payment_attempt.paid_at = payment_attempt.paid_at or now
    if not payment_already_in_refund:
        payment_attempt.last_error = ""
    payment_attempt.save(
        update_fields=(
            "status",
            "payment_method",
            "provider_payment_id",
            "provider_capture_id",
            "provider_transaction_id",
            "paid_at",
            "last_error",
            "updated_at",
        )
    )

    booking = Booking.objects.select_for_update().get(pk=payment_attempt.booking_id)
    payment_was_confirmed_before_cancellation = BookingEvent.objects.filter(
        booking=booking,
        event_type=BookingEventType.PAYMENT_CONFIRMED,
        details__payment_attempt_id=payment_attempt.pk,
    ).exists()
    paid_by_another_attempt = (
        booking.payment_attempts.exclude(pk=payment_attempt.pk)
        .filter(
            status__in=(
                PaymentStatus.PAID,
                PaymentStatus.REFUND_PENDING,
                PaymentStatus.REFUND_FAILED,
                PaymentStatus.REFUNDED,
            )
        )
        .exists()
    )
    if payment_was_confirmed_before_cancellation:
        outcome = PAYMENT_CONFIRMED
        event_type = BookingEventType.PAYMENT_CONFIRMED
    elif paid_by_another_attempt:
        # A second capture is always a duplicate, even when the booking has
        # since been cancelled or its start date has passed. Treating it as a
        # new sale could create a credit note against the original receipt.
        outcome = DUPLICATE_PAYMENT
        event_type = BookingEventType.DUPLICATE_PAYMENT_RECEIVED
    elif booking.status == BookingStatus.CANCELLED:
        outcome = PAYMENT_AFTER_CANCELLATION
        event_type = BookingEventType.PAYMENT_RECEIVED_AFTER_CANCELLATION
    elif (
        attempt_status_before_payment == PaymentStatus.CANCELLED
        or booking.status == BookingStatus.COMPLIMENTARY
        or payment_attempt.amount_eur != booking.total_price_eur
    ):
        outcome = PAYMENT_AFTER_PRICE_CHANGE
        event_type = BookingEventType.PAYMENT_RECEIVED_AFTER_PRICE_CHANGE
    elif (
        booking.earliest_start_date is not None
        and booking.earliest_start_date < timezone.localdate()
    ):
        outcome = PAYMENT_AFTER_SERVICE_START
        event_type = BookingEventType.PAYMENT_RECEIVED_AFTER_SERVICE_START
    else:
        outcome = PAYMENT_CONFIRMED
        event_type = BookingEventType.PAYMENT_CONFIRMED
        booking.status = BookingStatus.PAID
    if outcome == PAYMENT_CONFIRMED:
        booking.paid_at = booking.paid_at or now
        booking.confirmed_at = booking.confirmed_at or now
        if not booking.toconline_sale_document_id:
            booking.document_status = BookingDocumentStatus.PENDING
        booking.save(
            update_fields=(
                "status",
                "paid_at",
                "confirmed_at",
                "document_status",
                "updated_at",
            )
        )
    if not BookingEvent.objects.filter(
        booking=booking,
        event_type=event_type,
        details__payment_attempt_id=payment_attempt.pk,
    ).exists():
        BookingEvent.objects.create(
            booking=booking,
            event_type=event_type,
            actor=booking.customer.user,
            details={
                "payment_attempt_id": payment_attempt.pk,
                "captured_amount_eur": str(payment_attempt.amount_eur),
                "current_booking_total_eur": str(booking.total_price_eur),
                "requires_fiscal_review": outcome
                in {PAYMENT_AFTER_PRICE_CHANGE, PAYMENT_AFTER_SERVICE_START},
            },
        )
    payment_attempt.booking = booking
    return PaymentCompletion(booking=booking, outcome=outcome)


def _existing_payment_outcome(payment_attempt):
    booking = payment_attempt.booking
    if BookingEvent.objects.filter(
        booking=booking,
        event_type=BookingEventType.PAYMENT_CONFIRMED,
        details__payment_attempt_id=payment_attempt.pk,
    ).exists():
        return PAYMENT_CONFIRMED
    if booking.status == BookingStatus.CANCELLED:
        return PAYMENT_AFTER_CANCELLATION
    if BookingEvent.objects.filter(
        booking=booking,
        event_type=BookingEventType.PAYMENT_RECEIVED_AFTER_PRICE_CHANGE,
        details__payment_attempt_id=payment_attempt.pk,
    ).exists():
        return PAYMENT_AFTER_PRICE_CHANGE
    if BookingEvent.objects.filter(
        booking=booking,
        event_type=BookingEventType.PAYMENT_RECEIVED_AFTER_SERVICE_START,
        details__payment_attempt_id=payment_attempt.pk,
    ).exists():
        return PAYMENT_AFTER_SERVICE_START
    if (
        booking.payment_attempts.exclude(pk=payment_attempt.pk)
        .filter(
            status__in=(
                PaymentStatus.PAID,
                PaymentStatus.REFUND_PENDING,
                PaymentStatus.REFUND_FAILED,
                PaymentStatus.REFUNDED,
            )
        )
        .exists()
    ):
        return DUPLICATE_PAYMENT
    return PAYMENT_CONFIRMED


def _complete_payment_side_effects(completion, payment_attempt, *, client=None):
    if completion.outcome == PAYMENT_CONFIRMED:
        from activities.services.fulfilments import activate_confirmed_booking_fulfilments
        from discounts.services import consume_redemption
        from hotel.services.pricing import booking_purchase_reference

        consume_redemption(booking_purchase_reference(completion.booking))
        activate_confirmed_booking_fulfilments(completion.booking)
        _issue_document_without_reverting_payment(completion.booking)
        _send_payment_confirmation(payment_attempt)
        return

    if completion.outcome in {
        PAYMENT_AFTER_CANCELLATION,
        PAYMENT_AFTER_SERVICE_START,
    }:
        _issue_document_without_reverting_payment(completion.booking)

    _request_unexpected_payment_refund(payment_attempt, client=client)
    _send_unexpected_payment_notice(payment_attempt, outcome=completion.outcome)


def _request_unexpected_payment_refund(payment_attempt, *, client=None):
    try:
        request_refund(payment_attempt, payment_attempt.amount_eur, client=client)
    except PaymentGatewayError:
        logger.exception(
            "Could not request an automatic refund for an unexpected payment",
            extra={
                "booking_id": payment_attempt.booking_id,
                "payment_attempt_id": payment_attempt.pk,
            },
        )


def refund_requires_booking_credit_note(payment_attempt):
    """Return whether this refund corrects the booking's recorded sale.

    Duplicate captures and obsolete-price captures never created a sale of
    their own. They must therefore never amend the booking's original receipt.
    """
    if (
        payment_attempt.status != PaymentStatus.REFUNDED
        or payment_attempt.refund_amount_eur is None
    ):
        return False
    return not BookingEvent.objects.filter(
        booking_id=payment_attempt.booking_id,
        event_type__in=(
            BookingEventType.DUPLICATE_PAYMENT_RECEIVED,
            BookingEventType.PAYMENT_RECEIVED_AFTER_PRICE_CHANGE,
        ),
        details__payment_attempt_id=payment_attempt.pk,
    ).exists()


def issue_confirmed_booking_credit_note(payment_attempt):
    """Issue the corrective fiscal document only after a confirmed refund.

    Duplicate payments have no sale document of their own, so refunding one
    must never create a credit note against the booking's original receipt.
    """
    from hotel.services.invoicing import issue_booking_credit_note

    payment_attempt = PaymentAttempt.objects.select_related("booking").get(pk=payment_attempt.pk)
    if not refund_requires_booking_credit_note(payment_attempt):
        return ""

    refund_percentage = payment_attempt.refund_amount_eur / payment_attempt.amount_eur
    try:
        return issue_booking_credit_note(payment_attempt.booking, refund_percentage)
    except Exception:
        logger.exception(
            "TOConline credit note failed after a confirmed refund",
            extra={
                "booking_id": payment_attempt.booking_id,
                "payment_attempt_id": payment_attempt.pk,
            },
        )
        return ""


def _verified_paid_payment(payment_attempt, payment, *, notified_payload=None):
    if not _provider_payment_is_paid(payment):
        raise PaymentGatewayError("Easypay has not confirmed this payment.")
    _verify_payment_identity(payment_attempt, payment)

    if notified_payload:
        notified_amount = _money(notified_payload.get("value"))
        if notified_amount is not None and notified_amount != payment_attempt.amount_eur:
            raise PaymentGatewayError("The notified amount does not match the booking total.")
        notified_currency = notified_payload.get("currency")
        if notified_currency not in (None, "", payment_attempt.currency):
            raise PaymentGatewayError("The notified currency does not match the booking.")

    successful_captures = [
        capture
        for capture in payment.get("captures") or []
        if str(capture.get("status") or "").lower() in ("success", "refunded")
    ]
    if not successful_captures:
        raise PaymentGatewayError("Easypay returned no successful capture.")
    capture_id = str(successful_captures[0].get("id") or "")
    if not capture_id:
        raise PaymentGatewayError("Easypay returned an invalid capture id.")

    transactions = payment.get("transactions") or []
    method = (payment.get("method") or {}).get("type") or ""
    return {
        "capture_id": capture_id,
        "transaction_id": str(transactions[0].get("id") or "") if transactions else "",
        "method": str(method).upper(),
    }


def _provider_payment_is_paid(payment):
    return str(payment.get("payment_status") or payment.get("status") or "").lower() == "paid"


def _verify_payment_identity(payment_attempt, payment):
    if str(payment.get("key") or "") != payment_attempt.merchant_reference:
        raise PaymentGatewayError("The payment reference does not match.")
    if _money(payment.get("value")) != payment_attempt.amount_eur:
        raise PaymentGatewayError("The paid amount does not match the booking total.")
    if str(payment.get("currency") or "") != payment_attempt.currency:
        raise PaymentGatewayError("The payment currency does not match the booking.")


def _record_failed_payment_notification(payment_attempt, payload, *, fingerprint):
    """Acknowledge a verified provider failure without confirming the booking."""
    with transaction.atomic():
        locked_attempt = PaymentAttempt.objects.select_for_update().get(pk=payment_attempt.pk)
        PaymentEvent.objects.get_or_create(
            fingerprint=fingerprint,
            defaults={
                "payment_attempt": locked_attempt,
                "event_type": str(payload.get("type") or "capture"),
                "provider_event_id": str(payload.get("id") or ""),
                "provider_status": str(payload.get("status") or "failed"),
            },
        )
        if locked_attempt.status == PaymentStatus.PENDING:
            locked_attempt.status = PaymentStatus.FAILED
            locked_attempt.last_error = _(
                "The payment was not completed. You can safely try again."
            )
            locked_attempt.save(update_fields=("status", "last_error", "updated_at"))
    return locked_attempt


def _process_chargeback_notification(
    payment_attempt,
    payload,
    *,
    fingerprint,
    client,
):
    """Block a disputed booking without rewriting its financial history."""
    if not payment_attempt.provider_capture_id:
        raise PaymentGatewayError("The disputed payment has no provider capture id.")

    chargebacks = verified_chargebacks_for_capture(
        client,
        capture_id=payment_attempt.provider_capture_id,
        payment_id=payment_attempt.provider_payment_id,
        maximum_amount=payment_attempt.amount_eur,
    )
    chargeback_ids = [str(chargeback["id"]) for chargeback in chargebacks]
    total_amount = sum(
        (_money(chargeback.get("amount", chargeback.get("value"))) for chargeback in chargebacks),
        Decimal("0.00"),
    )

    with transaction.atomic():
        locked_attempt = (
            PaymentAttempt.objects.select_for_update()
            .select_related("booking")
            .get(pk=payment_attempt.pk)
        )
        _payment_event, created = PaymentEvent.objects.get_or_create(
            fingerprint=fingerprint,
            defaults={
                "payment_attempt": locked_attempt,
                "event_type": "chargeback",
                "provider_event_id": chargeback_ids[-1],
                "provider_status": str(payload.get("status") or "received"),
                "details": {
                    "chargeback_ids": chargeback_ids,
                    "amount_eur": str(total_amount),
                    "capture_id": locked_attempt.provider_capture_id,
                    "requires_fiscal_review": True,
                },
            },
        )
        if not created:
            return locked_attempt

        locked_attempt.status = PaymentStatus.DISPUTED
        locked_attempt.last_error = _(
            "The payment was disputed. Financial and fiscal review is required."
        )
        locked_attempt.save(update_fields=("status", "last_error", "updated_at"))

        booking = Booking.objects.select_for_update().get(pk=locked_attempt.booking_id)
        if booking.status == BookingStatus.PAID:
            booking.status = BookingStatus.PAYMENT_DISPUTED
            booking.save(update_fields=("status", "updated_at"))
        BookingEvent.objects.get_or_create(
            booking=booking,
            event_type=BookingEventType.PAYMENT_DISPUTED,
            details__payment_attempt_id=locked_attempt.pk,
            defaults={
                "details": {
                    "payment_attempt_id": locked_attempt.pk,
                    "chargeback_ids": chargeback_ids,
                    "amount_eur": str(total_amount),
                    "requires_fiscal_review": True,
                }
            },
        )
        locked_attempt.booking = booking
    return locked_attempt


def verified_chargebacks_for_capture(
    client,
    *,
    capture_id,
    payment_id,
    maximum_amount,
):
    """Fetch and validate chargebacks before trusting a webhook notification."""
    chargebacks = client.list_chargebacks(capture_id)
    if not chargebacks:
        raise PaymentGatewayError("Easypay has not confirmed this chargeback.")

    maximum_amount = _money(maximum_amount)
    verified = []
    total_amount = Decimal("0.00")
    for chargeback in chargebacks:
        if not isinstance(chargeback, dict) or not chargeback.get("id"):
            raise PaymentGatewayError("Easypay returned an invalid chargeback record.")
        capture = chargeback.get("capture") or {}
        if str(capture.get("id") or "") != str(capture_id):
            raise PaymentGatewayError("The chargeback capture does not match.")
        provider_payment_id = str(capture.get("payment_id") or "")
        if payment_id and provider_payment_id and provider_payment_id != str(payment_id):
            raise PaymentGatewayError("The chargeback payment does not match.")
        amount = _money(chargeback.get("amount", chargeback.get("value")))
        if amount is None or amount <= Decimal("0.00"):
            raise PaymentGatewayError("The chargeback amount is invalid.")
        total_amount += amount
        verified.append(chargeback)

    if maximum_amount is None or total_amount > maximum_amount:
        raise PaymentGatewayError("The chargeback amount exceeds the captured payment.")
    return tuple(verified)


def _issue_document_without_reverting_payment(booking):
    from hotel.services.invoicing import issue_booking_document

    try:
        issue_booking_document(booking)
    except Exception:
        logger.exception(
            "TOConline document issuance failed after a confirmed payment",
            extra={"booking_id": booking.pk},
        )


def _send_payment_confirmation(payment_attempt):
    payment_attempt.refresh_from_db(fields=("confirmation_sent_at",))
    if payment_attempt.confirmation_sent_at:
        return

    booking = payment_attempt.booking
    booking_path = reverse("hotel:booking_detail", kwargs={"pk": booking.pk})
    booking_url = f"{settings.PUBLIC_BASE_URL}{booking_path}"
    try:
        send_mail(
            _email_subject(booking.pk),
            (
                _(
                    "Hello %(name)s,\n\n"
                    "We received payment for booking #%(booking_id)s.\n"
                    "Booking details: %(booking_url)s\n\n"
                    "Thank you for choosing Olhó Cão."
                )
                % {
                    "name": booking.customer.user.get_full_name() or booking.customer.user.username,
                    "booking_id": booking.pk,
                    "booking_url": booking_url,
                }
            ),
            settings.DEFAULT_FROM_EMAIL,
            [booking.customer.user.email],
            using="default",
        )
    except Exception:
        logger.exception(
            "Could not send payment confirmation email",
            extra={"booking_id": booking.pk},
        )
        return

    payment_attempt.confirmation_sent_at = timezone.now()
    payment_attempt.save(update_fields=("confirmation_sent_at", "updated_at"))


def _send_unexpected_payment_notice(payment_attempt, *, outcome):
    payment_attempt.refresh_from_db(fields=("confirmation_sent_at", "status"))
    if payment_attempt.confirmation_sent_at:
        return

    booking = payment_attempt.booking
    user = booking.customer.user
    booking_url = (
        f"{settings.PUBLIC_BASE_URL}{reverse('hotel:booking_detail', kwargs={'pk': booking.pk})}"
    )
    if outcome == PAYMENT_AFTER_CANCELLATION:
        subject = _("Payment received after cancellation of booking #%(booking_id)s") % {
            "booking_id": booking.pk,
        }
        explanation = _("We received a payment after this booking had already been cancelled.")
    elif outcome == PAYMENT_AFTER_PRICE_CHANGE:
        subject = _("Obsolete payment detected for booking #%(booking_id)s") % {
            "booking_id": booking.pk,
        }
        explanation = _(
            "We received payment through an old link after the booking price changed. "
            "The booking was not charged again at the new price."
        )
    elif outcome == PAYMENT_AFTER_SERVICE_START:
        subject = _("Late payment detected for booking #%(booking_id)s") % {
            "booking_id": booking.pk,
        }
        explanation = _(
            "We received payment after the booking start date had passed, so the "
            "booking was not confirmed."
        )
    else:
        subject = _("Duplicate payment detected for booking #%(booking_id)s") % {
            "booking_id": booking.pk,
        }
        explanation = _("We detected an additional payment for a booking already paid.")

    refund_message = _(
        "The automatic refund was requested. It may take a few business days to appear."
    )
    if payment_attempt.status == PaymentStatus.REFUND_FAILED:
        refund_message = _(
            "The automatic refund could not be requested immediately. "
            "Our team has been alerted and will resolve it."
        )

    try:
        send_mail(
            subject,
            _(
                "Hello %(name)s,\n\n"
                "%(explanation)s\n"
                "%(refund_message)s\n\n"
                "Booking details: %(booking_url)s"
            )
            % {
                "name": user.get_full_name() or user.username,
                "explanation": explanation,
                "refund_message": refund_message,
                "booking_url": booking_url,
            },
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            using="default",
        )
    except Exception:
        logger.exception(
            "Could not send unexpected payment notification",
            extra={
                "booking_id": booking.pk,
                "payment_attempt_id": payment_attempt.pk,
            },
        )
        return

    payment_attempt.confirmation_sent_at = timezone.now()
    payment_attempt.save(update_fields=("confirmation_sent_at", "updated_at"))


def _email_subject(booking_id):
    return _("Your booking #%(booking_id)s is confirmed!") % {
        "booking_id": booking_id,
    }


def _notification_fingerprint(payload):
    canonical_payload = {
        "id": payload.get("id"),
        "key": payload.get("key"),
        "type": payload.get("type"),
        "status": payload.get("status"),
        "date": payload.get("date"),
        "transaction_id": (payload.get("transaction") or {}).get("id"),
    }
    serialized = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _provider_idempotency_key(*parts):
    value = "-".join(str(part) for part in parts if part not in (None, ""))
    if len(value) <= 50:
        return value
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:50]


def _money(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError) as exc:
        raise PaymentGatewayError("The payment amount is invalid.") from exc
