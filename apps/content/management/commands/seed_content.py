"""
python manage.py seed_content

Loads the content that is currently hardcoded in the storefront bundle
(Alreef_front/src/data/site.js and the About / FAQ / Shipping pages) into the
content tables, so the dashboard has something real to edit and the
storefront can switch to the API without losing a word of copy.

Idempotent: re-running updates the same rows (matched by slug / dial / url)
instead of duplicating them. Nothing here is invented — every fact comes from
the owner-confirmed data file.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.content.models import (
    Branch,
    ContactPhone,
    FaqItem,
    Page,
    PageSection,
    SectionKind,
    SiteSettings,
    SocialLink,
    SocialPlatform,
)
from apps.content.policy_pages import create_missing

SITE = {
    "name_ar": "الريف",
    "full_name_ar": "عسل نحل الريف",
    "name_en": "El Rif",
    "legal_name_ar": "شركة الريف للمواد الغذائية",
    "descriptor_ar": "عسل ومنتجات النحل",
    "descriptor_en": "Honey & bee products",
    "tagline_ar": "الريف، على سفرتك",
    "tagline_en": "El Rif, at your table",
    "founded_year": None,
    "site_url": "https://alreef-honey.com",
    "meta_title_ar": "عسل نحل الريف — عسل ومنتجات النحل",
    "meta_description_ar": (
        "عسل نحل الريف: عسل ومنتجات النحل من الفرع الرئيسي في طنطا، محافظة الغربية."
    ),
}

PHONES = [
    {"display": "+20 15 54112325", "dial": "201554112325", "is_whatsapp": True, "is_primary": True, "sort_order": 0},
]

BRANCHES = [
    {
        "name": "الفرع الرئيسي",
        "address": "طريق طنطا المحلة أمام محطة قطار حصة شبشير",
        "region": "الغربية",
        "plus_code": "V35C+RPF",
        "postal_code": "6647775",
        "maps_url": (
            "https://maps.google.com/?q=V35C%2BRPF+%D8%B4%D8%A8%D8%B4%D9%8A%D8%B1"
            "+%D8%A7%D9%84%D8%AD%D8%B5%D8%A9+%D8%B7%D9%86%D8%B7%D8%A7"
        ),
        "sort_order": 0,
    },
]

SOCIAL = [
    {"platform": SocialPlatform.FACEBOOK, "label": "فيسبوك", "url": "https://www.facebook.com/AlreeForHony/", "sort_order": 0},
    {"platform": SocialPlatform.INSTAGRAM, "label": "إنستجرام", "url": "https://www.instagram.com/AlreeForHony/", "sort_order": 1},
    {"platform": SocialPlatform.TIKTOK, "label": "تيك توك", "url": "https://www.tiktok.com/@alreeforhony", "sort_order": 2},
]

ABOUT_SECTIONS = [
    {
        "key": "who-we-are",
        "kind": SectionKind.TEXT,
        "eyebrow": "مين احنا",
        "title": "فرع حقيقي، مش مجرد صفحة على الإنترنت",
        "body": (
            "الفرع الرئيسي على طريق طنطا المحلة أمام محطة قطار حصة شبشير، وتقدر تعدّي عليه "
            "في مواعيد العمل. الموقع ده امتداد للفرع الرئيسي، مش بديل عنه: نفس المنتجات "
            "اللي على الرف، بنفس الأسماء المكتوبة على العبوات."
        ),
        "sort_order": 0,
    },
    {
        "key": "value-clarity",
        "kind": SectionKind.CARD,
        "title": "الوضوح",
        "body": "اسم المنتج ومكوّناته ووزنه مكتوبين قدامك. اللي ما نعرفوش، بنقول عليه مش متأكدين.",
        "icon": "information",
        "sort_order": 1,
    },
    {
        "key": "value-detail",
        "kind": SectionKind.CARD,
        "title": "الاهتمام بالتفاصيل",
        "body": "التعبئة والتغليف بيتراجعوا قبل الشحن عشان الزجاج يوصل سليم.",
        "icon": "package",
        "sort_order": 2,
    },
    {
        "key": "value-response",
        "kind": SectionKind.CARD,
        "title": "الرد",
        "body": "سؤال قبل الطلب أو بعده — في حد بيرد على رقم الفرع الرئيسي.",
        "icon": "contact",
        "sort_order": 3,
    },
]

PAGES = [
    {
        "slug": "about",
        "title_ar": "عن الريف",
        "title_en": "About",
        "subtitle_ar": "الريف، على سفرتك",
        "body_ar": (
            "الريف اسم قريب من البيت. ومع كل اختيار لسفرتك، يهمّنا تكون التفاصيل واضحة: اسم "
            "المنتج، ومكوّناته، وطريقة استخدامه. الاهتمام بيبدأ قبل الطلب، ويكمّل بعد ما يوصل."
        ),
        "meta_title": "عن الريف — عسل ومنتجات النحل",
        "meta_description": (
            "عسل نحل الريف: شركة الريف للمواد الغذائية، والفرع الرئيسي في "
            "طنطا، محافظة الغربية."
        ),
        "sort_order": 0,
        "sections": ABOUT_SECTIONS,
    },
    {
        "slug": "contact",
        "title_ar": "تواصل معنا",
        "title_en": "Contact",
        "subtitle_ar": "أسرع طريقة: اتصال أو واتساب على رقم الفرع الرئيسي. أو ابعتلنا سؤالك من هنا واحنا نرجعلك.",
        "meta_title": "تواصل معنا — الريف",
        "meta_description": (
            "رقم وعنوان الفرع الرئيسي لعسل نحل الريف في طنطا، محافظة الغربية، ونموذج للأسئلة عن المنتجات والطلبات."
        ),
        "sort_order": 1,
        "sections": [],
    },
    {
        "slug": "shipping",
        "title_ar": "الشحن والدفع",
        "title_en": "Shipping & payment",
        "subtitle_ar": "القواعد اللي بنشتغل بيها دلوقتي، من غير وعود زيادة.",
        "meta_title": "الشحن والدفع — الريف",
        "sort_order": 2,
        "sections": [
            {
                "key": "delivery-window",
                "kind": SectionKind.CARD,
                "title": "ميعاد التسليم",
                "body": "بنأكده معاك بالتليفون بعد مراجعة الطلب، لأنه بيختلف حسب المحافظة وشركة الشحن.",
                "icon": "delivery",
                "sort_order": 0,
            },
        ],
    },
    {
        "slug": "faq",
        "title_ar": "أسئلة متكررة",
        "title_en": "FAQ",
        "meta_title": "أسئلة متكررة — الريف",
        "meta_description": "إجابات عن الطلب والشحن والدفع واختيار نوع العسل وطريقة الحفظ.",
        "sort_order": 3,
        "sections": [],
    },
]

FAQS = [
    {
        "question": "إزاي أطلب؟",
        "answer": (
            "اختار المنتج والوزن، ضيفه للسلة، وبعدين اضغط إتمام الطلب. مش محتاج تسجّل حساب — "
            "بندخل الاسم والرقم والعنوان بس. بعد ما تأكد، بنكلّمك على الرقم اللي كتبته."
        ),
        "topic": "الطلب",
    },
    {
        "question": "الشحن بكام وبيوصل إمتى؟",
        "answer": (
            "مصاريف الشحن وحد الشحن المجاني معروضين في صفحة الشحن والدفع. ميعاد التسليم بيختلف "
            "حسب المحافظة، وبنأكده معاك بالتليفون بعد ما نراجع الطلب."
        ),
        "topic": "الشحن",
    },
    {
        "question": "أقدر أدفع إزاي؟",
        "answer": "الدفع عند الاستلام أو فودافون كاش. الموقع مش بيطلب بيانات بطاقة بنكية.",
        "topic": "الدفع",
    },
    {
        "question": "أختار أنهي نوع؟",
        "answer": (
            "لو الاستخدام يومي على الفطار، البرسيم الحجازي أو الزهور البرية. لو بتحب الطعم "
            "الخفيف، الموالح. لو بتدوّر على طعم أقوى، السدر. صفحة «أساعدك تختار» فيها ترشيحات حسب الاستخدام."
        ),
        "topic": "المنتجات",
    },
    {
        "question": "الأسعار دي للعبوة كاملة؟",
        "answer": (
            "أيوة. وتحت كل سعر بنكتب سعر الـ100 جم عشان تقدر تقارن بين الأحجام والأنواع بشكل حقيقي."
        ),
        "topic": "المنتجات",
    },
    {
        "question": "العسل بيتحبب — في مشكلة؟",
        "answer": (
            "لأ. التحبّب سلوك طبيعي في العسل مع الوقت. لو حبيت ترجّع القوام سايح، حط العبوة في ماء دافئ."
        ),
        "topic": "المنتجات",
    },
    {
        "question": "أحفظ العسل إزاي؟",
        "answer": (
            "في مكان جاف بعيد عن ضوء الشمس المباشر. مش محتاج تلاجة، ما عدا منتجات مكتوب عليها "
            "إنها تتحفظ مبرّدة زي غذاء الملكات."
        ),
        "topic": "المنتجات",
    },
    {
        "question": "أقدر أشتري من الفرع الرئيسي؟",
        "answer": "أيوة. الفرع الرئيسي على طريق طنطا المحلة أمام محطة قطار حصة شبشير.",
        "topic": "الفرع الرئيسي",
    },
]


class Command(BaseCommand):
    help = "Seed site settings, contact details, pages and FAQ from the storefront's confirmed content."

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write("Seeding site settings...")
        settings_row = SiteSettings.load()
        for field, value in SITE.items():
            setattr(settings_row, field, value)
        settings_row.save()

        self.stdout.write("Seeding phones, branches and social links...")
        ContactPhone.objects.exclude(dial__in=[phone["dial"] for phone in PHONES]).delete()
        for phone in PHONES:
            ContactPhone.objects.update_or_create(dial=phone["dial"], defaults=phone)
        for branch in BRANCHES:
            Branch.objects.update_or_create(plus_code=branch["plus_code"], defaults=branch)
        for link in SOCIAL:
            SocialLink.objects.update_or_create(url=link["url"], defaults=link)

        self.stdout.write("Seeding pages...")
        for page_data in PAGES:
            sections = page_data.pop("sections", [])
            page, _ = Page.objects.update_or_create(slug=page_data["slug"], defaults=page_data)
            for section in sections:
                PageSection.objects.update_or_create(page=page, key=section["key"], defaults=section)
            page_data["sections"] = sections  # keep the module-level data intact

        self.stdout.write("Seeding policy pages...")
        created = create_missing(Page, PageSection, email=settings_row.email)
        self.stdout.write(f"  created: {', '.join(created) or 'none (already there)'}")

        self.stdout.write("Seeding FAQ...")
        for index, faq in enumerate(FAQS):
            FaqItem.objects.update_or_create(
                question=faq["question"], defaults={**faq, "sort_order": index}
            )

        self.stdout.write(self.style.SUCCESS("Site content seeded."))
