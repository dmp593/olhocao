from django import template
from django.utils.translation import get_language

register = template.Library()


@register.filter
def product_name(product):
    if not product.metadata:
        return product.name

    lang = get_language()

    if f'name_{lang}' in product.metadata:
        return product.metadata[f'name_{lang}']

    return product.name
