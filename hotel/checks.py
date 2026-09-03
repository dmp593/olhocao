from decimal import Decimal

from django.conf import settings
from django.core.checks import Error, Warning, register
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

ALLOWED_EASYPAY_ENVIRONMENTS = {"sandbox", "production"}
ALLOWED_EASYPAY_METHODS = {"AP", "CC", "DD", "GP", "MB", "MBW", "VI"}


@register()
def payment_configuration_check(app_configs, **kwargs):
    messages = []
    environment = settings.EASYPAY_ENVIRONMENT.lower()
    methods = set(settings.EASYPAY_PAYMENT_METHODS)

    if environment not in ALLOWED_EASYPAY_ENVIRONMENTS:
        messages.append(
            Error(
                "EASYPAY_ENVIRONMENT must be 'sandbox' or 'production'.",
                id="hotel.E001",
            )
        )
    if unknown_methods := methods - ALLOWED_EASYPAY_METHODS:
        messages.append(
            Error(
                f"Unsupported EASYPAY_PAYMENT_METHODS: {sorted(unknown_methods)}.",
                id="hotel.E002",
            )
        )
    if not methods:
        messages.append(Error("At least one Easypay payment method is required.", id="hotel.E003"))

    refund_percentage = Decimal(str(settings.HOTEL_REFUND_PERCENTAGE))
    if not Decimal("0") <= refund_percentage <= Decimal("1"):
        messages.append(Error("HOTEL_REFUND_PERCENTAGE must be between 0 and 1.", id="hotel.E004"))

    if environment == "production":
        required_values = {
            "EASYPAY_ACCOUNT_ID": settings.EASYPAY_ACCOUNT_ID,
            "EASYPAY_API_KEY": settings.EASYPAY_API_KEY,
            "EASYPAY_WEBHOOK_USERNAME": settings.EASYPAY_WEBHOOK_USERNAME,
            "EASYPAY_WEBHOOK_PASSWORD": settings.EASYPAY_WEBHOOK_PASSWORD,
        }
        for name, value in required_values.items():
            if not value:
                messages.append(Error(f"{name} is required in production.", id="hotel.E005"))
        if not settings.PUBLIC_BASE_URL.startswith("https://"):
            messages.append(
                Error(
                    "PUBLIC_BASE_URL must use HTTPS in production.",
                    id="hotel.E006",
                )
            )

    if settings.EASYPAY_WEBHOOK_PASSWORD and len(settings.EASYPAY_WEBHOOK_PASSWORD) < 24:
        messages.append(
            Warning(
                "EASYPAY_WEBHOOK_PASSWORD should contain at least 24 characters.",
                id="hotel.W001",
            )
        )
    return messages


@register(deploy=True)
def production_integration_configuration_check(app_configs, **kwargs):
    messages = []
    if settings.EASYPAY_ENVIRONMENT.lower() != "production":
        messages.append(
            Error(
                "EASYPAY_ENVIRONMENT must be 'production' for deployment.",
                id="hotel.E010",
            )
        )

        required_easypay_values = {
            "EASYPAY_ACCOUNT_ID": settings.EASYPAY_ACCOUNT_ID,
            "EASYPAY_API_KEY": settings.EASYPAY_API_KEY,
            "EASYPAY_WEBHOOK_USERNAME": settings.EASYPAY_WEBHOOK_USERNAME,
            "EASYPAY_WEBHOOK_PASSWORD": settings.EASYPAY_WEBHOOK_PASSWORD,
        }
        for name, value in required_easypay_values.items():
            if not value:
                messages.append(Error(f"{name} is required for deployment.", id="hotel.E011"))

    invalid_toconline_values = {"", "fake-client-id", "fake-client-secret"}
    required_toconline_values = {
        "TOCONLINE_OAUTH_CLIENT_ID": settings.TOCONLINE_OAUTH_CLIENT_ID,
        "TOCONLINE_OAUTH_CLIENT_SECRET": settings.TOCONLINE_OAUTH_CLIENT_SECRET,
    }
    for name, value in required_toconline_values.items():
        if value in invalid_toconline_values:
            messages.append(Error(f"{name} is required for deployment.", id="hotel.E012"))

    https_values = {
        "PUBLIC_BASE_URL": settings.PUBLIC_BASE_URL,
        "TOCONLINE_BASE_URL": settings.TOCONLINE_BASE_URL,
        "TOCONLINE_OAUTH_REDIRECT_URI": settings.TOCONLINE_OAUTH_REDIRECT_URI,
    }
    if settings.TOCONLINE_OAUTH_BASE_URL:
        https_values["TOCONLINE_OAUTH_BASE_URL"] = settings.TOCONLINE_OAUTH_BASE_URL
    for name, value in https_values.items():
        if not value.startswith("https://"):
            messages.append(Error(f"{name} must use HTTPS for deployment.", id="hotel.E013"))

    email_values = {
        "DEFAULT_FROM_EMAIL": settings.DEFAULT_FROM_EMAIL,
        "SUPPORT_EMAIL": settings.SUPPORT_EMAIL,
    }
    for name, value in email_values.items():
        try:
            validate_email(value)
        except ValidationError:
            messages.append(Error(f"{name} must be a valid email address.", id="hotel.E014"))
    return messages
