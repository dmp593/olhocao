from enum import StrEnum


class TocOnlineResource(StrEnum):
    CUSTOMERS = 'customers'
    PRODUCTS = 'products'
    SERVICES = 'services'
    COMMERCIAL_SALES_DOCUMENTS = 'v1/commercial_sales_documents'
    ITEM_FAMILY = 'item_families'
    UNIT_OF_MEASURE = 'units_of_measure'
