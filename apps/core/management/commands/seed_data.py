"""
python manage.py seed_data

Seeds realistic Egyptian food/honey e-commerce reference + demo data:
governorates, categories, brands, attributes, products/variants with
inventory batches, shipping zones/methods/rates, a coupon, a promotion,
an admin user and a test customer. No real customer data is used.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from djmoney.money import Money

from apps.attributes.models import Attribute, AttributeInputType, AttributeValue
from apps.brands.models import Brand
from apps.catalog.models import PackagingType, Product, ProductStatus, ProductType, ProductVariant, TaxCategory, VariantAttributeValue
from apps.categories.models import Category
from apps.coupons.models import Coupon, CouponDiscountType
from apps.inventory.models import Batch, InventoryItem
from apps.promotions.models import Promotion, PromotionType
from apps.shipping.models import (
    EGYPT_GOVERNORATES,
    Governorate,
    RateCalculationType,
    ShippingMethod,
    ShippingMethodType,
    ShippingRate,
    ShippingZone,
)

User = get_user_model()


class Command(BaseCommand):
    help = "Seed the database with realistic Egyptian food/honey demo data."

    def add_arguments(self, parser):
        parser.add_argument("--flush-demo", action="store_true", help="Delete previously seeded demo products before reseeding.")

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write("Seeding governorates...")
        governorates = self._seed_governorates()

        self.stdout.write("Seeding shipping...")
        self._seed_shipping(governorates)

        self.stdout.write("Seeding categories & brands...")
        categories = self._seed_categories()
        brands = self._seed_brands()

        self.stdout.write("Seeding attributes...")
        attributes = self._seed_attributes()

        self.stdout.write("Seeding products...")
        self._seed_products(categories, brands, attributes)

        self.stdout.write("Seeding coupons & promotions...")
        self._seed_coupons_promotions(categories)

        self.stdout.write("Seeding users...")
        self._seed_users()

        self.stdout.write(self.style.SUCCESS("Seed data complete."))

    def _seed_governorates(self):
        governorates = {}
        for i, (code, name_en, name_ar) in enumerate(EGYPT_GOVERNORATES):
            gov, _ = Governorate.objects.get_or_create(
                code=code, defaults={"name_en": name_en, "name_ar": name_ar, "sort_order": i}
            )
            governorates[code] = gov
        return governorates

    def _seed_shipping(self, governorates):
        cairo_giza, _ = ShippingZone.objects.get_or_create(name="Cairo & Giza")
        cairo_giza.governorates.set([governorates["cairo"], governorates["giza"]])

        rest_of_egypt, _ = ShippingZone.objects.get_or_create(name="Rest of Egypt")
        rest_of_egypt.governorates.set([g for code, g in governorates.items() if code not in ("cairo", "giza")])

        standard, _ = ShippingMethod.objects.get_or_create(
            name="Standard Delivery", defaults={"method_type": ShippingMethodType.STANDARD, "min_delivery_days": 2, "max_delivery_days": 4}
        )
        express, _ = ShippingMethod.objects.get_or_create(
            name="Express Delivery", defaults={"method_type": ShippingMethodType.EXPRESS, "min_delivery_days": 1, "max_delivery_days": 2}
        )

        ShippingRate.objects.get_or_create(
            zone=cairo_giza, method=standard,
            defaults={"calculation_type": RateCalculationType.FLAT, "base_price": Money(45, "EGP"), "free_shipping_threshold": Money(1000, "EGP")},
        )
        ShippingRate.objects.get_or_create(
            zone=cairo_giza, method=express,
            defaults={"calculation_type": RateCalculationType.FLAT, "base_price": Money(75, "EGP")},
        )
        ShippingRate.objects.get_or_create(
            zone=rest_of_egypt, method=standard,
            defaults={"calculation_type": RateCalculationType.FLAT, "base_price": Money(65, "EGP"), "free_shipping_threshold": Money(1500, "EGP")},
        )

    def _seed_categories(self):
        names = [
            ("honey", "Honey", "عسل"),
            ("dates", "Dates", "تمر"),
            ("nuts", "Nuts", "مكسرات"),
            ("spices", "Spices", "بهارات"),
            ("oils", "Oils", "زيوت"),
            ("grocery", "Grocery", "بقالة"),
            ("beverages", "Beverages", "مشروبات"),
            ("snacks", "Snacks", "وجبات خفيفة"),
            ("gift-boxes", "Gift Boxes", "علب هدايا"),
        ]
        categories = {}
        for i, (slug, name_en, name_ar) in enumerate(names):
            cat, _ = Category.objects.get_or_create(slug=slug, defaults={"name_en": name_en, "name_ar": name_ar, "sort_order": i})
            categories[slug] = cat
        return categories

    def _seed_brands(self):
        names = ["Alreef Farms", "Sinai Naturals", "Nile Valley Foods", "Oasis Harvest"]
        brands = {}
        for name in names:
            brand, _ = Brand.objects.get_or_create(name=name, defaults={"slug": name.lower().replace(" ", "-"), "country_of_origin": "Egypt"})
            brands[name] = brand
        return brands

    def _seed_attributes(self):
        weight_attr, _ = Attribute.objects.get_or_create(
            code="weight", defaults={"name_en": "Weight", "name_ar": "الوزن", "input_type": AttributeInputType.SELECT, "is_variant_attribute": True, "unit": "g"}
        )
        weight_values = {}
        for i, (label, grams) in enumerate([("250g", 250), ("500g", 500), ("1kg", 1000)]):
            val, _ = AttributeValue.objects.get_or_create(
                attribute=weight_attr, slug=label, defaults={"value_en": label, "value_ar": label, "sort_order": i}
            )
            weight_values[grams] = val

        origin_attr, _ = Attribute.objects.get_or_create(
            code="origin", defaults={"name_en": "Origin", "name_ar": "المنشأ", "input_type": AttributeInputType.SELECT, "is_variant_attribute": False}
        )
        for i, origin in enumerate(["Sinai", "Fayoum", "Siwa", "Aswan"]):
            AttributeValue.objects.get_or_create(attribute=origin_attr, slug=origin.lower(), defaults={"value_en": origin, "value_ar": origin, "sort_order": i})

        return {"weight": weight_attr, "weight_values": weight_values, "origin": origin_attr}

    def _seed_products(self, categories, brands, attributes):
        weight_values = attributes["weight_values"]
        brand_list = list(brands.values())

        product_defs = [
            {
                "name": "Sidr Honey", "category": "honey", "sku": "HONEY-SIDR", "price": Decimal("450.00"),
                "honey_metadata": {"honey_type": "Sidr", "floral_source": "Ziziphus spina-christi", "origin": "Sinai", "raw_honey": True, "organic": True, "filtered": False},
                "food_metadata": {"storage_instructions": "Store in a cool, dry place.", "shelf_life": "24 months", "country_of_origin": "Egypt"},
                "packaging": PackagingType.JAR, "tax": TaxCategory.ZERO_RATED,
            },
            {
                "name": "Black Seed Honey", "category": "honey", "sku": "HONEY-BLACKSEED", "price": Decimal("380.00"),
                "honey_metadata": {"honey_type": "Black Seed Infused", "origin": "Fayoum", "raw_honey": True, "organic": False},
                "food_metadata": {"storage_instructions": "Store in a cool, dry place.", "shelf_life": "18 months"},
                "packaging": PackagingType.JAR, "tax": TaxCategory.ZERO_RATED,
            },
            {
                "name": "Premium Medjool Dates", "category": "dates", "sku": "DATES-MEDJOOL", "price": Decimal("220.00"),
                "food_metadata": {
                    "ingredients": "100% dates", "allergens": [], "storage_instructions": "Store in a cool, dry place.",
                    "country_of_origin": "Egypt", "nutritional_information": {"calories_per_100g": 277, "sugar_g": 66},
                },
                "packaging": PackagingType.BOX, "tax": TaxCategory.ZERO_RATED,
            },
            {
                "name": "Roasted Mixed Nuts", "category": "nuts", "sku": "NUTS-MIXED", "price": Decimal("310.00"),
                "food_metadata": {
                    "ingredients": "Almonds, cashews, walnuts, peanuts, salt", "allergens": ["tree nuts", "peanuts"],
                    "storage_instructions": "Store in an airtight container.", "shelf_life": "6 months",
                },
                "packaging": PackagingType.POUCH, "tax": TaxCategory.STANDARD,
            },
            {
                "name": "Cold-Pressed Olive Oil", "category": "oils", "sku": "OIL-OLIVE", "price": Decimal("280.00"),
                "food_metadata": {"ingredients": "100% extra virgin olive oil", "country_of_origin": "Egypt", "storage_instructions": "Store away from light and heat."},
                "packaging": PackagingType.BOTTLE, "tax": TaxCategory.ZERO_RATED,
            },
            {
                "name": "Tahini", "category": "grocery", "sku": "GROCERY-TAHINI", "price": Decimal("150.00"),
                "food_metadata": {"ingredients": "100% roasted sesame seeds", "allergens": ["sesame"], "shelf_life": "12 months"},
                "packaging": PackagingType.JAR, "tax": TaxCategory.STANDARD,
            },
        ]

        for definition in product_defs:
            product, created = Product.objects.get_or_create(
                sku=definition["sku"],
                defaults={
                    "name_en": definition["name"], "name_ar": definition["name"], "slug": definition["sku"].lower(),
                    "description": f"Premium quality {definition['name']}, sourced and packed in Egypt.",
                    "short_description": f"{definition['name']} — natural & high quality.",
                    "brand": brand_list[hash(definition["sku"]) % len(brand_list)],
                    "category": categories[definition["category"]],
                    "product_type": ProductType.VARIABLE, "status": ProductStatus.ACTIVE, "published": True,
                    "price": Money(definition["price"], "EGP"),
                    "tax_category": definition["tax"], "packaging_type": definition["packaging"],
                    "food_metadata": definition.get("food_metadata", {}), "honey_metadata": definition.get("honey_metadata", {}),
                    "badges": ["ORGANIC"] if definition.get("honey_metadata", {}).get("organic") else [],
                },
            )
            if not created:
                continue

            for grams, weight_value in weight_values.items():
                multiplier = {250: Decimal("0.6"), 500: Decimal("1.0"), 1000: Decimal("1.8")}[grams]
                variant = ProductVariant.objects.create(
                    product=product, sku=f"{definition['sku']}-{grams}", name_suffix=f"{grams}g",
                    price=Money(definition["price"] * multiplier, "EGP"), weight_grams=grams,
                    is_default=(grams == 500),
                )
                VariantAttributeValue.objects.create(variant=variant, attribute_value=weight_value)

                item = InventoryItem.objects.create(variant=variant, low_stock_threshold=10)
                Batch.objects.create(
                    item=item, batch_number=f"{variant.sku}-B1", production_date=date.today() - timedelta(days=30),
                    expiration_date=date.today() + timedelta(days=540), initial_quantity=100, quantity=100,
                    cost_price=Money(definition["price"] * multiplier * Decimal("0.6"), "EGP"),
                )

    def _seed_coupons_promotions(self, categories):
        Coupon.objects.get_or_create(
            code="WELCOME10",
            defaults={
                "description": "10% off your first order", "discount_type": CouponDiscountType.PERCENTAGE,
                "percentage_value": Decimal("10.00"), "minimum_order_amount": Money(200, "EGP"), "per_user_limit": 1,
            },
        )
        promo, created = Promotion.objects.get_or_create(
            name="Honey Week", defaults={"promotion_type": PromotionType.PERCENTAGE, "percentage_value": Decimal("15.00"), "priority": 10}
        )
        if created:
            promo.categories.add(categories["honey"])

    def _seed_users(self):
        if not User.objects.filter(email="admin@alreef.example").exists():
            User.objects.create_superuser(email="admin@alreef.example", password="ChangeMe123!", first_name="Admin")
            self.stdout.write(self.style.WARNING("Created admin@alreef.example / ChangeMe123! — change this password immediately."))

        if not User.objects.filter(email="customer@alreef.example").exists():
            customer = User.objects.create_user(
                email="customer@alreef.example", password="ChangeMe123!", first_name="Test", last_name="Customer",
                email_verified=True,
            )
            governorate = Governorate.objects.filter(code="cairo").first()
            if governorate:
                from apps.users.models import Address

                Address.objects.create(
                    user=customer, label="home", full_name="Test Customer", phone="+201000000000",
                    governorate=governorate, street="10 Tahrir St", building="5", is_default=True,
                )
            self.stdout.write(self.style.WARNING("Created customer@alreef.example / ChangeMe123! — for local testing only."))
