from unittest.mock import Mock

from django.test import SimpleTestCase, override_settings
from toconline.api.resources import Resource, ResourceCollection

from olhocao.integrations.toconline import TocOnlineGateway, TocOnlineGatewayError


def resource(resource_type, resource_id, **attributes):
    return Resource(
        {
            "type": resource_type,
            "id": resource_id,
            "attributes": attributes,
        }
    )


def collection(resource_type, *resource_ids):
    return ResourceCollection(
        {
            "data": [
                {
                    "type": resource_type,
                    "id": resource_id,
                    "attributes": {"name": f"Item {resource_id}"},
                }
                for resource_id in resource_ids
            ]
        }
    )


class FakeHttpResponse:
    def __init__(self, content, *, content_length=None):
        self.content = content
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset : offset + chunk_size]

    def close(self):
        self.closed = True


class TocOnlineGatewayTests(SimpleTestCase):
    def setUp(self):
        self.transport = Mock()
        self.gateway = TocOnlineGateway(self.transport)

    def test_customer_operations_use_the_v2_company_namespace(self):
        self.transport.resources.company.list_customers.return_value = collection(
            "customers", "customer-1"
        )
        self.transport.resources.company.get_customer.return_value = resource(
            "customers", "customer-1", business_name="Ana Silva"
        )
        self.transport.resources.company.create_customer.return_value = resource(
            "customers", "customer-2", business_name="Bruno Costa"
        )

        found = self.gateway.find_customer(email="ana@example.com")
        retrieved = self.gateway.get_customer("customer-1")
        created = self.gateway.create_customer(business_name="Bruno Costa")
        self.gateway.update_customer("customer-1", business_name="Ana Matos")

        self.assertEqual(found["id"], "customer-1")
        self.assertEqual(retrieved["attributes"]["business_name"], "Ana Silva")
        self.assertEqual(created["id"], "customer-2")
        self.transport.resources.company.list_customers.assert_called_once_with(
            params={"filter[email]": "ana@example.com", "page[size]": 1}
        )
        self.transport.resources.company.update_customer.assert_called_once_with(
            body={
                "data": {
                    "id": "customer-1",
                    "attributes": {"business_name": "Ana Matos"},
                }
            }
        )

    def test_catalog_operations_return_plain_resource_mappings(self):
        self.transport.resources.auxiliaries.list_item_families.return_value = collection(
            "item_families", "family-1"
        )
        self.transport.resources.catalog.list_services.return_value = collection(
            "services", "service-1"
        )
        self.transport.resources.catalog.list_products.return_value = collection(
            "products", "product-1"
        )

        families = self.gateway.list_item_families()
        services = self.gateway.list_services()
        products = self.gateway.list_products()

        self.assertEqual(families[0]["id"], "family-1")
        self.assertEqual(services[0]["id"], "service-1")
        self.assertEqual(products[0]["id"], "product-1")
        self.transport.resources.auxiliaries.list_item_families.assert_called_once_with(
            params={"page[size]": 1000}
        )
        self.transport.resources.catalog.list_services.assert_called_once_with(
            params={"filter[is_active]": "true", "page[size]": 1000}
        )
        self.transport.resources.catalog.list_products.assert_called_once_with(
            params={"filter[is_active]": "true", "page[size]": 1000}
        )

    def test_sales_operations_use_the_v2_sales_namespace(self):
        self.transport.resources.sales.list_sales_documents.return_value = ResourceCollection(
            {
                "data": [
                    {
                        "type": "commercial_sales_documents",
                        "id": "sale-1",
                        "attributes": {
                            "document_no": "FR 2026/1",
                            "external_reference": "BOOKING-1",
                        },
                    }
                ]
            }
        )
        self.transport.resources.sales.get_sales_document.return_value = resource(
            "commercial_sales_documents", "sale-1", document_no="FR 2026/1"
        )
        self.transport.resources.sales.create_sales_document.return_value = {"id": "sale-2"}

        found = self.gateway.find_sales_document(external_reference="BOOKING-1")
        retrieved = self.gateway.get_sales_document("sale-1")
        created = self.gateway.create_sales_document(document_type="FR", lines=[])

        self.assertEqual(found["id"], "sale-1")
        self.assertEqual(retrieved["attributes"]["document_no"], "FR 2026/1")
        self.assertEqual(created["id"], "sale-2")
        self.transport.resources.sales.list_sales_documents.assert_called_once_with()
        self.transport.resources.sales.create_sales_document.assert_called_once_with(
            body={"document_type": "FR", "lines": []}
        )

    def test_sales_lookup_does_not_return_an_unrelated_document(self):
        self.transport.resources.sales.list_sales_documents.return_value = collection(
            "commercial_sales_documents", "unrelated-sale"
        )

        document = self.gateway.find_sales_document(external_reference="BOOKING-1")

        self.assertIsNone(document)

    @override_settings(TOCONLINE_DOCUMENT_MAX_BYTES=1024, TOCONLINE_TIMEOUT=7)
    def test_pdf_download_accepts_only_a_bounded_toconline_document(self):
        self.transport.api.documents.get_sales_document_print_url.return_value = {
            "data": {
                "attributes": {
                    "url": {
                        "scheme": "https",
                        "host": "integration.toconline.pt",
                        "port": 443,
                        "path": "/public-file/document-token",
                    }
                }
            }
        }
        response = FakeHttpResponse(b"%PDF-1.7\nvalid", content_length=14)
        http_get = Mock(return_value=response)
        gateway = TocOnlineGateway(self.transport, http_get=http_get)

        content = gateway.download_sales_document("sale-1")

        self.assertEqual(content, b"%PDF-1.7\nvalid")
        self.assertTrue(response.closed)
        http_get.assert_called_once_with(
            "https://integration.toconline.pt:443/public-file/document-token",
            timeout=7,
            allow_redirects=False,
            stream=True,
        )

    def test_pdf_download_rejects_an_untrusted_host_without_requesting_it(self):
        self.transport.api.documents.get_sales_document_print_url.return_value = {
            "data": {
                "attributes": {
                    "url": {
                        "scheme": "https",
                        "host": "toconline.pt.attacker.example",
                        "path": "/public-file/document-token",
                    }
                }
            }
        }
        http_get = Mock()
        gateway = TocOnlineGateway(self.transport, http_get=http_get)

        with self.assertRaises(TocOnlineGatewayError):
            gateway.download_sales_document("sale-1")

        http_get.assert_not_called()

    @override_settings(TOCONLINE_DOCUMENT_MAX_BYTES=8)
    def test_pdf_download_closes_an_oversized_response(self):
        self.transport.api.documents.get_sales_document_print_url.return_value = {
            "data": {
                "attributes": {
                    "url": {
                        "scheme": "https",
                        "host": "integration.toconline.pt",
                        "path": "/public-file/document-token",
                    }
                }
            }
        }
        response = FakeHttpResponse(b"%PDF-too-large", content_length=14)
        gateway = TocOnlineGateway(self.transport, http_get=Mock(return_value=response))

        with self.assertRaises(TocOnlineGatewayError):
            gateway.download_sales_document("sale-1")

        self.assertTrue(response.closed)
