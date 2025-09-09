from enum import StrEnum


# Official API documentation: https://api-docs.toconline.pt/


class TocOnlineResource(StrEnum):
    # COMPANY > CUSTOMERS
    CUSTOMERS = 'customers'

    # COMPANY > SUPPLIERS
    SUPPLIERS = 'suppliers'

    # COMPANY > COMMON TO CUSTOMERS AND SUPPLIERS
    ADDRESSES = 'addresses'

    # COMPANY > PRODUCTS AND SERVICES
    PRODUCTS = 'products'
    SERVICES = 'services'

    # SALES
    COMMERCIAL_SALES_DOCUMENTS = 'v1/commercial_sales_documents'
    COMMERCIAL_SALES_RECEIPTS = 'v1/commercial_sales_receipts'

    # PURCHASES
    COMMERCIAL_PURCHASES_DOCUMENTS = 'v1/commercial_purchases_documents'
    COMMERCIAL_PURCHASES_PAYMENTS = 'v1/commercial_purchases_payments'

    # AUXILIARIES
    TAX_DESCRIPTORS = 'tax_descriptors'
    ITEM_FAMILIES = 'item_families'
    COUNTRIES = 'countries'
    UNITS_OF_MEASURE = 'units_of_measure'
    BANK_ACCOUNTS = 'bank_accounts'
    CASH_ACCOUNTS = 'cash_accounts'
    CURRENCIES = 'currencies'
    TAXES = 'taxes'
    EXPENSE_CATEGORIES = 'expense_categories'
    COMMERCIAL_DOCUMENT_SERIES = 'commercial_document_series'


class TocOnlineDocumentType(StrEnum):
    DOCUMENT = 'Document'
    RECEIPT = 'Receipt'
    PURCHASE_DOCUMENT = 'PurchaseDocument'
