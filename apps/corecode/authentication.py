import os

from django.conf import settings
from rest_framework import authentication, exceptions


class ExternalAPIKeyAuthentication(authentication.BaseAuthentication):
    """Authenticate requests using a static shared API key in the X-API-KEY header."""

    def authenticate(self, request):
        api_key = request.headers.get("X-API-KEY")
        expected_key = getattr(settings, "EXTERNAL_API_KEY", None)

        if not expected_key:
            raise exceptions.AuthenticationFailed(
                "External API key is not configured on this server."
            )

        if not api_key:
            raise exceptions.AuthenticationFailed(
                "Missing X-API-KEY header."
            )

        if api_key != expected_key:
            raise exceptions.AuthenticationFailed(
                "Invalid X-API-KEY header value."
            )

        return ("external-api-client", None)
