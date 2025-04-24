from base64 import b64encode
from datetime import datetime, timedelta
from enum import StrEnum
from django.conf import settings

from cachetools import cached, TTLCache
import requests


cache_list = TTLCache(maxsize=1024, ttl=300)
cache_get = TTLCache(maxsize=1024, ttl=300)


class TocOnlineCredentials:
    client_id: str
    client_secret: str
    redirect_uri: str

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
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


class TocOnlineToken:
    access_token: str
    refresh_token: str

    aquired_at: datetime
    expires_at: datetime

    token_type: str

    def __init__(
        self,
        access_token: str,
        refresh_token: str,
        expires_in: int,
        token_type: str
    ):
        self.access_token = access_token
        self.refresh_token = refresh_token

        self.aquired_at = datetime.now()
        self.expires_at = self.aquired_at + timedelta(seconds=expires_in)

        self.token_type = token_type


class TocOnlineResource(StrEnum):
    CUSTOMERS = 'customers'
    PRODUCTS = 'products'
    SERVICES = 'services'
    COMERCIAL_SALES_DOCUMENTS = 'commercial_sales_documents'
    ITEM_FAMILY = 'item_families'
    UNIT_OF_MEASURE = 'units_of_measure'


class TocOnline:
    base_url: str
    credentials: TocOnlineCredentials
    token: TocOnlineToken

    def __init__(
        self,
        base_url: str,
        credentials: TocOnlineCredentials,
    ):
        self.base_url = base_url
        self.credentials = credentials
        self.token = None

    @property
    def default_headers(self):
        headers = {
            'Accept': 'application/json',
        }

        if self.token:
            headers['Authorization'] = f"Bearer {self.token.access_token}"

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
            timeout=7,
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
            timeout=7,
        )

        return response.json()

    def _refresh_access_token(self):
        response = requests.post(
            f"{self.base_url}/oauth/token",
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json',
                'Authorization': f'Basic {self.credentials.b64}'
            },
            data={
                'refresh_token': self.token.refresh_token,
                'grant_type': 'refresh_token',
                'scope': 'commercial',
            },
            timeout=7,
        )

        return response.json()

    def authenticate(self):
        authorization_code = self._get_authorization_code()

        self.token = TocOnlineToken(
           **self._get_access_token(authorization_code)
        )

    def refresh(self):
        if not self.token:
            raise ValueError("Token is not set. Please authenticate first.")

        token = self._refresh_access_token()

        self.token = TocOnlineToken(
            access_token=token['access_token'],
            refresh_token=token.get('refresh_token', self.token.refresh_token),
            expires_in=token['expires_in'],
            token_type=token['token_type'],
        )

    def ensure_authenticated(self, raise_exception: bool = True):
        if not self.token:
            self.authenticate()

        if not self.token:
            if raise_exception:
                raise RuntimeError("Can't authenticate.")
            return False

        if self.token.expires_at <= datetime.now():
            self.refresh()

        if self.token.expires_at <= datetime.now():
            if raise_exception:
                raise RuntimeError("Can't re-authenticate.")
            return False

        return True

    def clear_cached(self):
        cache_list.clear()
        cache_get.clear()

    # cache data for no longer than five minutes
    @cached(cache=cache_list)
    def list(
        self,
        resource: TocOnlineResource | str,
        limit: str | int | None = None,
        **kwargs
    ):
        self.ensure_authenticated()

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
            timeout=7,
        )

        response.raise_for_status()
        return response.json()['data']

    def first(self, resource: TocOnlineResource | str, **kwargs):
        data = self.list(resource, limit=1, **kwargs)

        if len(data) == 0:
            return None

        return data[0]

    # cache data for no longer than five minutes
    @cached(cache=cache_get)
    def get(
        self,
        resource: TocOnlineResource | str,
        pk: str
    ):
        self.ensure_authenticated()

        response = requests.get(
            f"{self.base_url}/api/{resource}/{pk}",
            headers=self.default_headers,
            timeout=7,
        )

        response.raise_for_status()
        return response.json()['data']

    def create(
        self,
        resource: TocOnlineResource | str,
        **kwargs
    ):
        self.ensure_authenticated()

        response = requests.post(
            f"{self.base_url}/api/{resource}",
            headers=self.default_headers,
            json={
                "data": {
                    "attributes": kwargs,
                    "type": resource
                }
            },
            timeout=7
        )

        response.raise_for_status()
        self.clear_cached()

        return response.json()['data']
    
    def update(
        self,
        resource: TocOnlineResource | str,
        pk: str,
        **kwargs
    ):
        self.ensure_authenticated()

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
            timeout=7
        )

        response.raise_for_status()
        self.clear_cached()

        return response.json()['data']

    def delete(
        self,
        resource: TocOnlineResource | str,
        pk: str
    ):
        self.ensure_authenticated()

        response = requests.delete(
            f"{self.base_url}/api/{resource}/{pk}",
            headers=self.default_headers,
            timeout=7
        )

        response.raise_for_status()
        self.clear_cached()


toconline = TocOnline(
    base_url=settings.TOC_ONLINE_BASE_URL,
    credentials=TocOnlineCredentials(
        client_id=settings.TOC_ONLINE_OAUTH_CLIENT_ID,
        client_secret=settings.TOC_ONLINE_OAUTH_CLIENT_SECRET,
        redirect_uri=settings.TOC_ONLINE_OAUTH_REDIRECT_URI,
    )
)
