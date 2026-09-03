from django import template

register = template.Library()


@register.filter
def product_name(product):
    return product.name
