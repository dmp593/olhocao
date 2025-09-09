from base64 import b64encode
# from datetime import datetime, timedelta, timezone
from django.conf import settings

# from django.db.models import (
#     F,
#     Value,
#     DateTimeField,
#     DurationField,
#     ExpressionWrapper
# )

# from django.db.models.functions import (
#     Coalesce,
#     Now
# )

import requests

from .resources import (
    TocOnlineResource, TocOnlineDocumentType
)

from .models import TocOnlineToken

from django.db import transaction


class TocOnlineCredentials:
    client_id: str
    client_secret: str
    redirect_uri: str

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    @property
    def b64(self) -> str:
        """
        Gets the base64 encoded credentials for the OAuth2 client.
        :return: The base64 encoded credentials.
        """
        crendentials = f"{self.client_id}:{self.client_secret}".encode('utf-8')
        return b64encode(crendentials).decode('utf-8')


class TocOnline:
    base_url: str
    credentials: TocOnlineCredentials
    timeout: int

    def __init__(
        self,
        base_url: str,
        credentials: TocOnlineCredentials,
        timeout: int
    ):
        self.base_url = base_url
        self.credentials = credentials
        self.timeout = timeout

    @property
    def default_headers(self) -> dict:
        headers = {
            'Accept': '*/*',  # According to the official docs.
        }

        token = self.get_token()

        if token:
            headers['Authorization'] = f"Bearer {token.access_token}"

        return headers

    def _get_authorization_code(self):
        response = requests.get(
            f"{self.base_url}/oauth/auth",
            params={
                'client_id': self.credentials.client_id,
                'redirect_uri': self.credentials.redirect_uri,
                'response_type': 'code',
                'scope': 'commercial',
            },
            allow_redirects=False,
            timeout=self.timeout,
        )

        return response.headers['Location'].split('code=')[1]

    def _get_access_token(self, authorization_code: str):
        response = requests.post(
            f"{self.base_url}/oauth/token",
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json',
                'Authorization': f'Basic {self.credentials.b64}'
            },
            data={
                'code': authorization_code,
                'grant_type': 'authorization_code',
                'scope': 'commercial',
            },
            timeout=self.timeout,
        )

        return response.json()

    def _refresh_access_token(self, refresh_token: str):
        response = requests.post(
            f"{self.base_url}/oauth/token",
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json',
                'Authorization': f'Basic {self.credentials.b64}'
            },
            data={
                'refresh_token': refresh_token,
                'grant_type': 'refresh_token',
                'scope': 'commercial',
            },
            timeout=self.timeout,
        )

        return response.json()

    @transaction.atomic
    def refresh_token(self, token):
        new_token = self._refresh_access_token(token.refresh_token)

        token.access_token = new_token.get(
            'access_token', token.access_token
        )

        token.refresh_token = new_token.get(
            'refresh_token', token.refresh_token
        )

        token.expires_in = new_token.get(
            'expires_in', token.expires_in
        )
        token.token_type = new_token.get(
            'token_type', token.token_type
        )

        token.save()

    # def get_token_queryset(self):
    #     # Find the latest non-expired token (with a small safety skew)
    #     skew = timedelta(seconds=30)

    #     # Define a 1 second timedelta for use in ttl calculation
    #     timedelta_1_sec = timedelta(seconds=1)

    #     # Use Coalesce to handle null refreshed_at values
    #     last_refreshed_at = Coalesce('refreshed_at', 'acquired_at')

    #     return TocOnlineToken.objects.annotate(
    #         last_refreshed_at=last_refreshed_at,
    #         ttl=ExpressionWrapper(
    #             Value(timedelta_1_sec) * F('expires_in'),
    #             output_field=DurationField(),
    #         ),
    #         exp_at=ExpressionWrapper(
    #             F('last_refreshed_at') + F('ttl'),
    #             output_field=DateTimeField()
    #         ),
    #     ).filter(
    #         exp_at__gt=Now() + skew
    #     ).order_by(
    #         last_refreshed_at.desc()
    #     )

    @transaction.atomic
    def get_token(self):
        token = TocOnlineToken.objects.order_by('-refreshed_at').first()

        if token:
            if not token.is_expired:
                return token

            token.delete()

        access_token = self._get_access_token(
            self._get_authorization_code()
        )

        token = TocOnlineToken.objects.create(
            **access_token
        )

        return token

    def _get_response_data(self, response: requests.Response):
        json_response = response.json()

        return (
            json_response['data']
            if 'data' in json_response
            else json_response
        )

    def list(
        self,
        resource: TocOnlineResource | str,
        limit: str | int | None = None,
        **kwargs
    ):
        params = {}

        if kwargs:
            for key, val in kwargs.items():
                params[f"filter[{key}]"] = val

        if limit:
            params['page[size]'] = limit

        response = requests.get(
            f"{self.base_url}/api/{resource}",
            params=params,
            headers=self.default_headers,
            timeout=self.timeout,
        )

        response.raise_for_status()
        return self._get_response_data(response)

    def first(self, resource: TocOnlineResource | str, **kwargs):
        data = self.list(resource, limit=1, **kwargs)

        if len(data) == 0:
            return None

        return data[0]

    def get(
        self,
        resource: TocOnlineResource | str,
        pk: str
    ):
        response = requests.get(
            f"{self.base_url}/api/{resource}/{pk}",
            headers=self.default_headers,
            timeout=self.timeout,
        )

        response.raise_for_status()
        return self._get_response_data(response)

    def create(
        self,
        resource: TocOnlineResource | str,
        **kwargs
    ):
        response = requests.post(
            f"{self.base_url}/api/{resource}",
            headers=self.default_headers,
            json={
                "data": {
                    "attributes": kwargs,
                    "type": resource,
                },
            }
            if resource not in (
                TocOnlineResource.COMMERCIAL_SALES_DOCUMENTS,
                TocOnlineResource.COMMERCIAL_PURCHASES_DOCUMENTS,
                TocOnlineResource.COMMERCIAL_PURCHASES_PAYMENTS,
            )
            else kwargs,

            timeout=self.timeout
        )

        response.raise_for_status()
        return self._get_response_data(response)

    def update(
        self,
        resource: TocOnlineResource | str,
        pk: str,
        **kwargs
    ):
        response = requests.patch(
            f"{self.base_url}/api/{resource}",
            headers=self.default_headers,
            json={
                "data": {
                    "attributes": kwargs,
                    "id": pk,
                    "type": resource
                }
            },
            timeout=self.timeout
        )

        response.raise_for_status()
        return self._get_response_data(response)

    def delete(
        self,
        resource: TocOnlineResource | str,
        pk: str
    ):
        response = requests.delete(
            f"{self.base_url}/api/{resource}/{pk}",
            headers=self.default_headers,
            timeout=self.timeout
        )

        response.raise_for_status()

    # Specific methods for documents

    def send_document_to_finantial_authority(
        self,
        document_id: str,
        **kwargs
    ):
        # TocOnlineResource.SEND_TO_FINANTIAL_AUTHORITY
        resource = 'send_document_at_webservice'

        response = requests.post(
            f"{self.base_url}/api/{resource}",
            headers=self.default_headers,
            json={
                "data": {
                    "type": resource,
                    "id": document_id,
                    "attributes": kwargs
                }
            },
            timeout=self.timeout
        )

        response.raise_for_status()
        return self._get_response_data(response)

    def cancel_document(
        self,
        resource: TocOnlineResource | str,
        document_id: str,
    ):
        if resource not in (
            TocOnlineResource.COMMERCIAL_SALES_RECEIPTS,
            TocOnlineResource.COMMERCIAL_PURCHASES_DOCUMENTS,
        ):
            raise ValueError(
                f"Resource '{resource}' does not support cancelation."
                " Only commercial sales receipts and purchases documents"
                " can be canceled."
            )

        response = requests.patch(
            f"{self.base_url}/api/{resource}/{document_id}/void",
            headers=self.default_headers,
            timeout=self.timeout
        )

        response.raise_for_status()
        return self._get_response_data(response)

    def send_document_via_email(
        self,
        from_email: str,
        from_name: str,
        subject: str,
        to_email: str,
        document_id: str,
        document_type: str = TocOnlineDocumentType.DOCUMENT
    ):
        response = self.update(
            'email/document',  # TocOnlineResource.SEND_MAIL
            document_id,
            from_email=from_email,
            from_name=from_name,
            subject=subject,
            to_email=to_email,
            type=document_type
        )

        response.raise_for_status()

    def download_document(
        self,
        document_id: str,
        document_type: str = TocOnlineDocumentType.DOCUMENT,
        number_of_copies: int = 1,
    ) -> bytes:
        response = requests.get(
            f"{self.base_url}/api/url_for_print/{document_id}",
            headers=self.default_headers,
            params={
                'filter[type]': document_type,
                'filter[copies]': max(1, number_of_copies)
            },
            timeout=self.timeout
        )

        response.raise_for_status()
        json_response = response.json()

        url_attrs = json_response.get('data').get('attributes', {}).get('url', {})
        url = f"{url_attrs['scheme']}://{url_attrs['host']}:{url_attrs['port']}{url_attrs['path']}"

        response = requests.get(url, timeout=10)
        response.raise_for_status()

        return response.content


toconline = TocOnline(
    base_url=settings.TOCONLINE_BASE_URL,
    credentials=TocOnlineCredentials(
        client_id=settings.TOCONLINE_OAUTH_CLIENT_ID,
        client_secret=settings.TOCONLINE_OAUTH_CLIENT_SECRET,
        redirect_uri=settings.TOCONLINE_OAUTH_REDIRECT_URI,
    ),
    timeout=settings.TOCONLINE_TIMEOUT
)
