"""
SKUs and barcodes the shop does not have to type.

Staff may still enter their own (a supplier's SKU, a GS1 barcode bought for
supermarket listing); a code is generated only when the field is left empty.

SKU
    Product:  ELR-00012            (from the product's id)
    Variant:  ELR-00012-500G       (the product's SKU + the weight), or
              ELR-00012-V37        (+ the variant's id when it has no weight)
    A generated SKU that someone already typed by hand gets the row's id
    appended, so the unique constraint never fails on a generated value.

Barcode
    EAN-13 in the 20–29 prefix range, which GS1 reserves for a retailer's
    own in-store use: it scans on any till and label printer but can never
    collide with a manufacturer's registered barcode. It is NOT a GS1 code
    and is not valid for selling through other retailers — those need a
    registered one (Egypt: 622…), which staff can type in instead.

        20 · kind (1 product, 2 variant) · id, 9 digits · check digit

    Built from the primary key, so two rows can never get the same code.
"""

from uuid import uuid4

SKU_PREFIX = "ELR"
INTERNAL_BARCODE_PREFIX = "20"
KIND_PRODUCT = "1"
KIND_VARIANT = "2"


def placeholder_sku() -> str:
    """Holds the unique column until the row has an id to build the real one from."""
    return f"TMP-{uuid4().hex}"


def ean13_check_digit(first12: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12))
    return str((10 - total % 10) % 10)


def is_valid_ean13(code: str) -> bool:
    return len(code) == 13 and code.isdigit() and ean13_check_digit(code[:12]) == code[12]


def internal_barcode(kind: str, pk: int) -> str:
    body = f"{INTERNAL_BARCODE_PREFIX}{kind}{pk:09d}"
    if len(body) != 12:
        raise ValueError(f"id {pk} is too large for an internal EAN-13")
    return body + ean13_check_digit(body)


def _unique(model, candidate: str, pk: int) -> str:
    if model.objects.filter(sku=candidate).exclude(pk=pk).exists():
        return f"{candidate}-{pk}"
    return candidate


def product_sku(product) -> str:
    return _unique(type(product), f"{SKU_PREFIX}-{product.pk:05d}", product.pk)


def variant_sku(variant) -> str:
    base = variant.product.sku
    suffix = f"{variant.weight_grams}G" if variant.weight_grams else f"V{variant.pk}"
    return _unique(type(variant), f"{base}-{suffix}", variant.pk)


def fill_codes(instance, *, kind: str, make_sku, save_super, args, kwargs):
    """Shared save() body: save once to get an id, then write the codes."""
    instance.sku = (instance.sku or "").strip()
    instance.barcode = (instance.barcode or "").strip()
    needs_sku = not instance.sku
    needs_barcode = not instance.barcode
    if needs_sku:
        instance.sku = placeholder_sku()
    save_super(*args, **kwargs)

    updates = {}
    if needs_sku:
        updates["sku"] = make_sku(instance)
    if needs_barcode:
        updates["barcode"] = internal_barcode(kind, instance.pk)
    if updates:
        for field, value in updates.items():
            setattr(instance, field, value)
        type(instance).objects.filter(pk=instance.pk).update(**updates)
