from collections.abc import Mapping
from urllib.parse import urlunsplit

import requests
from django.conf import settings
from pydantic import BaseModel
from toconline.api.resources import Resource, ResourceCollection
from toconline.services import toconline


class TocOnlineGatewayError(RuntimeError):
    """Raised when TOConline returns data that cannot be used safely."""


class TocOnlineGateway:
    """Application-facing adapter for django-toconline's generated v2 client."""

    def __init__(self, transport=toconline, *, http_get=requests.get):
        self.transport = transport
        self.http_get = http_get

    def find_customer(self, **filters):
        customers = self.transport.resources.company.list_customers(
            params=_json_api_collection_params(filters, page_size=1)
        )
        return _first_resource(customers)

    def get_customer(self, customer_id):
        response = self.transport.resources.company.get_customer(str(customer_id))
        return _single_resource(response)

    def create_customer(self, **attributes):
        response = self.transport.resources.company.create_customer(attributes=attributes)
        return _required_resource(response, "customer creation")

    def update_customer(self, customer_id, **attributes):
        self.transport.resources.company.update_customer(
            body={
                "data": {
                    "id": str(customer_id),
                    "attributes": attributes,
                }
            }
        )

    def list_item_families(self):
        response = self.transport.resources.auxiliaries.list_item_families(
            params={"page[size]": 1000}
        )
        return _resource_list(response)

    def list_services(self):
        response = self.transport.resources.catalog.list_services(
            params=_json_api_collection_params({"is_active": True}, page_size=1000)
        )
        return _resource_list(response)

    def list_products(self):
        response = self.transport.resources.catalog.list_products(
            params=_json_api_collection_params({"is_active": True}, page_size=1000)
        )
        return _resource_list(response)

    def find_catalog_item(self, item_type, *, item_code):
        params = _json_api_collection_params({"item_code": item_code}, page_size=1)
        if item_type == "Service":
            response = self.transport.resources.catalog.list_services(params=params)
        else:
            response = self.transport.resources.catalog.list_products(params=params)
        return _first_resource(response)

    def create_catalog_item(self, item_type, **attributes):
        resource_type = "services" if item_type == "Service" else "products"
        item_attributes = {
            "type": item_type,
            **attributes,
        }
        if item_type == "Service":
            response = self.transport.resources.catalog.create_service(
                body={
                    "data": [
                        {
                            "type": resource_type,
                            "attributes": item_attributes,
                        }
                    ]
                }
            )
        else:
            response = self.transport.resources.catalog.create_product(
                body={
                    "data": {
                        "type": resource_type,
                        "attributes": item_attributes,
                    }
                }
            )
        return _required_created_resource(response, "catalog item creation")

    def find_sales_document(self, *, external_reference):
        response = self.transport.resources.sales.list_sales_documents()
        for document in _resource_list(response):
            attributes = document.get("attributes") or document
            if attributes.get("external_reference") == external_reference:
                return document
        return None

    def get_sales_document(self, document_id):
        response = self.transport.resources.sales.get_sales_document(str(document_id))
        return _required_resource(response, "sales document retrieval")

    def create_sales_document(self, **payload):
        response = self.transport.resources.sales.create_sales_document(body=payload)
        return _required_resource(response, "sales document creation")

    def download_sales_document(self, document_id):
        response = self.transport.api.documents.get_sales_document_print_url(str(document_id))
        payload = _as_mapping(response)
        try:
            url_parts = payload["data"]["attributes"]["url"]
        except (KeyError, TypeError) as exc:
            raise TocOnlineGatewayError(
                "TOConline did not return a printable document URL."
            ) from exc

        document_url = _safe_document_url(url_parts)
        document_response = self.http_get(
            document_url,
            timeout=settings.TOCONLINE_TIMEOUT,
            allow_redirects=False,
            stream=True,
        )
        try:
            document_response.raise_for_status()
            return _read_pdf(document_response)
        finally:
            document_response.close()


def _as_mapping(value):
    if isinstance(value, Resource):
        return dict(value.raw)
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=True)
    if isinstance(value, Mapping):
        return dict(value)
    raise TocOnlineGatewayError("TOConline returned an unexpected response shape.")


def _single_resource(response):
    if response is None:
        return None
    payload = _as_mapping(response)
    data = payload.get("data")
    if isinstance(data, Mapping):
        return dict(data)
    return payload


def _required_resource(response, operation):
    resource = _single_resource(response)
    if not resource or not resource.get("id"):
        raise TocOnlineGatewayError(f"TOConline {operation} returned no resource id.")
    return resource


def _required_created_resource(response, operation):
    if isinstance(response, ResourceCollection):
        resources = [dict(resource.raw) for resource in response]
        resource = resources[0] if resources else None
        if resource and resource.get("id"):
            return resource
    payload = _as_mapping(response)
    data = payload.get("data", payload)
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, Mapping) or not data.get("id"):
        raise TocOnlineGatewayError(f"TOConline {operation} returned no resource id.")
    return dict(data)


def _resource_list(response):
    if response is None:
        return []
    if isinstance(response, ResourceCollection):
        return [dict(resource.raw) for resource in response]
    if isinstance(response, (list, tuple)):
        return [_single_resource(item) for item in response]

    payload = _as_mapping(response)
    data = payload.get("data", payload)
    if not isinstance(data, list):
        raise TocOnlineGatewayError("TOConline returned an unexpected collection shape.")
    return [_single_resource(item) for item in data]


def _first_resource(response):
    resources = _resource_list(response)
    return resources[0] if resources else None


def _safe_document_url(url_parts):
    if not isinstance(url_parts, Mapping):
        raise TocOnlineGatewayError("TOConline returned an invalid printable URL.")

    scheme = str(url_parts.get("scheme") or "")
    host = str(url_parts.get("host") or "").lower().rstrip(".")
    path = str(url_parts.get("path") or "")
    port = url_parts.get("port")

    if scheme != "https" or not host.endswith(".toconline.pt"):
        raise TocOnlineGatewayError("TOConline returned an untrusted printable URL.")
    if not path.startswith("/public-file/"):
        raise TocOnlineGatewayError("TOConline returned an invalid printable path.")
    if port not in (None, 443, 443.0, "443"):
        raise TocOnlineGatewayError("TOConline returned an unsupported printable URL port.")

    netloc = host if port is None else f"{host}:443"
    return urlunsplit((scheme, netloc, path, "", ""))


def _json_api_collection_params(filters, *, page_size):
    params = {"page[size]": page_size}
    for name, value in filters.items():
        if isinstance(value, bool):
            value = str(value).lower()
        params[f"filter[{name}]"] = value
    return params


def _read_pdf(response):
    maximum_bytes = settings.TOCONLINE_DOCUMENT_MAX_BYTES
    content_length = response.headers.get("Content-Length")
    try:
        declared_bytes = int(content_length) if content_length is not None else None
    except (TypeError, ValueError):
        declared_bytes = None
    if declared_bytes is not None and declared_bytes > maximum_bytes:
        raise TocOnlineGatewayError("The TOConline PDF exceeds the configured size limit.")

    chunks = []
    received_bytes = 0
    for chunk in response.iter_content(chunk_size=65536):
        if not chunk:
            continue
        received_bytes += len(chunk)
        if received_bytes > maximum_bytes:
            raise TocOnlineGatewayError("The TOConline PDF exceeds the configured size limit.")
        chunks.append(chunk)

    content = b"".join(chunks)
    if not content.startswith(b"%PDF-"):
        raise TocOnlineGatewayError("TOConline returned an invalid PDF document.")
    return content


toconline_gateway = TocOnlineGateway()
