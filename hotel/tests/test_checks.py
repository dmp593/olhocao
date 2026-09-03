from django.test import SimpleTestCase, override_settings

from hotel.checks import payment_configuration_check, production_integration_configuration_check


class ProductionConfigurationChecksTests(SimpleTestCase):
    @override_settings(
        EASYPAY_ENVIRONMENT="sandbox",
        EASYPAY_ACCOUNT_ID="",
        EASYPAY_API_KEY="",
        EASYPAY_WEBHOOK_USERNAME="",
        EASYPAY_WEBHOOK_PASSWORD="",
        TOCONLINE_OAUTH_CLIENT_ID="fake-client-id",
        TOCONLINE_OAUTH_CLIENT_SECRET="fake-client-secret",
        PUBLIC_BASE_URL="http://localhost:8000",
    )
    def test_deployment_rejects_sandbox_and_placeholder_credentials(self):
        errors = production_integration_configuration_check(None)
        error_ids = {error.id for error in errors}

        self.assertIn("hotel.E010", error_ids)
        self.assertIn("hotel.E011", error_ids)
        self.assertIn("hotel.E012", error_ids)
        self.assertIn("hotel.E013", error_ids)

    @override_settings(
        EASYPAY_ENVIRONMENT="production",
        EASYPAY_ACCOUNT_ID="account",
        EASYPAY_API_KEY="api-key",
        EASYPAY_WEBHOOK_USERNAME="webhook-user",
        EASYPAY_WEBHOOK_PASSWORD="webhook-password-with-entropy",
        TOCONLINE_OAUTH_CLIENT_ID="client-id",
        TOCONLINE_OAUTH_CLIENT_SECRET="client-secret",
        PUBLIC_BASE_URL="https://www.olhocao.pt",
        TOCONLINE_BASE_URL="https://api17.toconline.pt",
        TOCONLINE_OAUTH_BASE_URL="https://app17.toconline.pt/oauth",
        TOCONLINE_OAUTH_REDIRECT_URI="https://www.olhocao.pt/oauth/callback/",
    )
    def test_complete_production_integration_configuration_passes(self):
        self.assertEqual(production_integration_configuration_check(None), [])

    @override_settings(
        EASYPAY_ENVIRONMENT="production",
        EASYPAY_ACCOUNT_ID="",
        EASYPAY_API_KEY="",
        EASYPAY_WEBHOOK_USERNAME="",
        EASYPAY_WEBHOOK_PASSWORD="",
        TOCONLINE_OAUTH_CLIENT_ID="client-id",
        TOCONLINE_OAUTH_CLIENT_SECRET="client-secret",
        PUBLIC_BASE_URL="https://www.olhocao.pt",
        TOCONLINE_BASE_URL="https://api17.toconline.pt",
        TOCONLINE_OAUTH_REDIRECT_URI="https://oauth.pstmn.io/v1/callback",
    )
    def test_deployment_rejects_missing_production_payment_credentials(self):
        errors = payment_configuration_check(None)

        self.assertEqual([error.id for error in errors], ["hotel.E005"] * 4)

    @override_settings(
        EASYPAY_ENVIRONMENT="production",
        EASYPAY_ACCOUNT_ID="account",
        EASYPAY_API_KEY="api-key",
        EASYPAY_WEBHOOK_USERNAME="webhook-user",
        EASYPAY_WEBHOOK_PASSWORD="webhook-password-with-entropy",
        TOCONLINE_OAUTH_CLIENT_ID="client-id",
        TOCONLINE_OAUTH_CLIENT_SECRET="client-secret",
        PUBLIC_BASE_URL="https://www.olhocao.pt",
        TOCONLINE_BASE_URL="https://api17.toconline.pt",
        TOCONLINE_OAUTH_REDIRECT_URI="https://oauth.pstmn.io/v1/callback",
        SUPPORT_EMAIL="invalid-address",
    )
    def test_deployment_rejects_invalid_support_email(self):
        errors = production_integration_configuration_check(None)

        self.assertEqual([error.id for error in errors], ["hotel.E014"])

    @override_settings(
        EASYPAY_ENVIRONMENT="production",
        EASYPAY_ACCOUNT_ID="account",
        EASYPAY_API_KEY="api-key",
        EASYPAY_WEBHOOK_USERNAME="webhook-user",
        EASYPAY_WEBHOOK_PASSWORD="webhook-password-with-entropy",
        TOCONLINE_OAUTH_CLIENT_ID="client-id",
        TOCONLINE_OAUTH_CLIENT_SECRET="client-secret",
        PUBLIC_BASE_URL="https://www.olhocao.pt",
        TOCONLINE_BASE_URL="https://api17.toconline.pt",
        TOCONLINE_OAUTH_BASE_URL="http://app17.toconline.pt/oauth",
        TOCONLINE_OAUTH_REDIRECT_URI="https://www.olhocao.pt/oauth/callback/",
    )
    def test_deployment_rejects_insecure_optional_oauth_base_url(self):
        errors = production_integration_configuration_check(None)

        self.assertEqual([error.id for error in errors], ["hotel.E013"])
