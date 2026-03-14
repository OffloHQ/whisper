from django import template

from listings.utils import format_listing_price

register = template.Library()


@register.filter
def compact_price(value):
    return format_listing_price(value)
