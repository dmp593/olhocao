from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import gettext as _


def send_customer_invitation(user):
    """Send a password-creation link without ever creating a temporary password."""
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    path = reverse(
        "accounts:password_reset_confirm",
        kwargs={"uidb64": uid, "token": token},
    )
    invitation_url = f"{settings.PUBLIC_BASE_URL}{path}"
    name = user.get_full_name() or user.username
    send_mail(
        _("Create your OlhóCão password"),
        _(
            "Hello %(name)s,\n\n"
            "OlhóCão created your customer account. Use the secure link below "
            "to choose your password:\n%(url)s\n\n"
            "Your username is: %(username)s\n\n"
            "If you were not expecting this invitation, contact OlhóCão."
        )
        % {
            "name": name,
            "url": invitation_url,
            "username": user.username,
        },
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        using="default",
    )
