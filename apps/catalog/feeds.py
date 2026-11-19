"""
Google Merchant Center product feed.

    GET /api/v1/feeds/google-merchant.xml

RSS 2.0 with the `g:` namespace, one <item> per purchasable size (an active
variant of an active, published product). Merchant Center fetches it on a
schedule, so the body is cached for a while per host; prices and stock in the
feed may therefore trail the shop by up to FEED_CACHE_SECONDS.

Links point at the storefront (FRONTEND_URL, through apps.core.frontend_links,
never the request host). Image links are built the way the product API builds
them (absolute URLs from the request), because the media files are served by
the API side and the storage backend may already hand out absolute URLs.
"""
import re
import xml.etree.ElementTree as ET
from decimal import Decimal

from django.core.cache import cache
from django.http import HttpResponse
from django.utils.html import strip_tags
from django.views.decorators.http import require_GET

from apps.catalog.views import _published_products
from apps.core.frontend_links import frontend_url
from apps.inventory.services import get_or_create_item

G_NS = "http://base.google.com/ns/1.0"
ET.register_namespace("g", G_NS)

FEED_CACHE_SECONDS = 30 * 60
FEED_CACHE_KEY = "feeds:google-merchant:v1:{host}"

BRAND = "الريف"
SHOP_DESCRIPTION = "عسل نحل طبيعي ومنتجات النحل من الريف"
# Google's taxonomy path for honey. Given as text: Merchant Center accepts the
# full path as well as the numeric id, and the text cannot point at the wrong
# node.
GOOGLE_PRODUCT_CATEGORY = "Food, Beverages & Tobacco > Food Items > Condiments & Sauces > Honey"
SHIPPING_COUNTRY = "EG"

MAX_TITLE = 150
MAX_DESCRIPTION = 5000
MAX_ADDITIONAL_IMAGES = 10

# Characters XML 1.0 cannot carry at all (escaping does not help with these).
_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _clean(text) -> str:
    return _XML_ILLEGAL.sub("", str(text or "")).strip()


def _g(parent, tag, text):
    el = ET.SubElement(parent, f"{{{G_NS}}}{tag}")
    el.text = _clean(text)
    return el


def _money(money) -> str:
    amount = Decimal(money.amount).quantize(Decimal("0.01"))
    return f"{amount} {money.currency}"


def _size_label(variant) -> str:
    grams = variant.weight_grams
    if grams:
        if grams >= 1000 and grams % 1000 == 0:
            return f"{grams // 1000} كجم"
        return f"{grams} جم"
    return variant.name_suffix or ""


def _flat_shipping_price():
    """
    The one delivery fee the shop charges everywhere, or None.

    Shipping is priced per zone and method (apps.shipping). Merchant Center
    gets a country-wide price only when every active, non-pickup rate is the
    same flat (or free) amount; anything else would advertise a fee some
    shoppers do not pay, so the attribute is left out and Merchant Center
    falls back to the account-level shipping settings.
    """
    from apps.shipping.models import RateCalculationType, ShippingMethodType, ShippingRate

    rates = ShippingRate.objects.filter(
        is_active=True, zone__is_active=True, method__is_active=True,
    ).exclude(method__method_type=ShippingMethodType.PICKUP)
    prices = set()
    for rate in rates:
        if rate.calculation_type == RateCalculationType.FREE:
            prices.add(("0.00", str(rate.base_price.currency)))
        elif rate.calculation_type == RateCalculationType.FLAT:
            prices.add((str(Decimal(rate.base_price.amount).quantize(Decimal("0.01"))), str(rate.base_price.currency)))
        else:
            return None
    if len(prices) != 1:
        return None
    amount, currency = prices.pop()
    return f"{amount} {currency}"


def _images_for(product, variant):
    """The size's own photos first, then the product's shared ones; never
    another size's photos (they show a different jar)."""
    images = list(product.images.all())
    own = [i for i in images if i.variant_id == variant.id]
    shared = [i for i in images if i.variant_id is None]
    key = lambda i: (not i.is_primary, i.sort_order, i.pk)  # noqa: E731
    return sorted(own, key=key) + sorted(shared, key=key)


def _category_path(category) -> str:
    if category is None:
        return ""
    return " > ".join(c.name_ar or c.name_en for c in category.get_ancestors(include_self=True))


def build_feed(request) -> bytes:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = BRAND
    ET.SubElement(channel, "link").text = frontend_url("/")
    ET.SubElement(channel, "description").text = SHOP_DESCRIPTION

    shipping_price = _flat_shipping_price()
    category_paths = {}

    for product in _published_products().order_by("pk"):
        variants = sorted(
            (v for v in product.variants.all() if v.is_active),
            key=lambda v: (v.sort_order, v.pk),
        )
        many_sizes = len(variants) > 1
        description = _clean(strip_tags(product.description or product.short_description or product.name_ar))
        if product.category_id not in category_paths:
            category_paths[product.category_id] = _category_path(product.category)

        for variant in variants:
            images = _images_for(product, variant)
            if not images:
                # Merchant Center rejects an item without an image.
                continue

            label = _size_label(variant) if many_sizes or variant.weight_grams else ""
            title = f"{product.name_ar} {label}".strip()
            query = {"size": variant.weight_grams} if many_sizes and variant.weight_grams else {}

            item = ET.SubElement(channel, "item")
            _g(item, "id", variant.sku)
            _g(item, "item_group_id", product.sku or product.pk)
            ET.SubElement(item, "title").text = _clean(title)[:MAX_TITLE]
            ET.SubElement(item, "description").text = description[:MAX_DESCRIPTION]
            ET.SubElement(item, "link").text = frontend_url(f"/product/{product.slug}", **query)
            _g(item, "image_link", request.build_absolute_uri(images[0].image.url))
            for extra in images[1 : 1 + MAX_ADDITIONAL_IMAGES]:
                _g(item, "additional_image_link", request.build_absolute_uri(extra.image.url))

            in_stock = not get_or_create_item(variant).is_out_of_stock
            _g(item, "availability", "in_stock" if in_stock else "out_of_stock")

            compare = variant.compare_at_price
            if compare is not None and compare.amount > variant.price.amount:
                _g(item, "price", _money(compare))
                _g(item, "sale_price", _money(variant.price))
            else:
                _g(item, "price", _money(variant.price))

            _g(item, "brand", BRAND)
            _g(item, "condition", "new")
            _g(item, "google_product_category", GOOGLE_PRODUCT_CATEGORY)
            if category_paths[product.category_id]:
                _g(item, "product_type", category_paths[product.category_id])
            if variant.weight_grams:
                _g(item, "product_weight", f"{variant.weight_grams} g")
            if shipping_price:
                shipping = _g(item, "shipping", "")
                _g(shipping, "country", SHIPPING_COUNTRY)
                _g(shipping, "price", shipping_price)
            # Barcodes here are internal EAN-13s generated by the shop
            # (apps.catalog.codes), not registered GTINs.
            _g(item, "identifier_exists", "no")

    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


@require_GET
def google_merchant_feed(request):
    key = FEED_CACHE_KEY.format(host=request.get_host())
    body = cache.get(key)
    if body is None:
        body = build_feed(request)
        cache.set(key, body, FEED_CACHE_SECONDS)
    response = HttpResponse(body, content_type="application/xml; charset=utf-8")
    response["Cache-Control"] = f"public, max-age={FEED_CACHE_SECONDS}"
    return response
