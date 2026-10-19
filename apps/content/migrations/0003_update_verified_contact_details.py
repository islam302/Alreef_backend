import django.core.validators
from django.db import migrations, models


PHONE_DISPLAY = "+20 15 54112325"
PHONE_DIAL = "201554112325"
BRANCH_NAME = "الفرع الرئيسي"
BRANCH_ADDRESS = "طريق طنطا المحلة أمام محطة قطار حصة شبشير"


def update_verified_contact_details(apps, schema_editor):
    SiteSettings = apps.get_model("content", "SiteSettings")
    ContactPhone = apps.get_model("content", "ContactPhone")
    Branch = apps.get_model("content", "Branch")
    Page = apps.get_model("content", "Page")
    PageSection = apps.get_model("content", "PageSection")
    FaqItem = apps.get_model("content", "FaqItem")

    had_contact_phones = ContactPhone.objects.exists()
    ContactPhone.objects.all().delete()
    if had_contact_phones:
        ContactPhone.objects.create(
            display=PHONE_DISPLAY,
            dial=PHONE_DIAL,
            is_whatsapp=True,
            is_primary=True,
            sort_order=0,
            is_active=True,
        )

    branch = Branch.objects.filter(plus_code="V35C+RPF").order_by("id").first()
    if branch is None:
        branch = Branch.objects.filter(name__in=["المحل", BRANCH_NAME]).order_by("id").first()
    if branch is not None:
        branch.name = BRANCH_NAME
        branch.address = BRANCH_ADDRESS
        branch.region = "الغربية"
        branch.postal_code = branch.postal_code or "6647775"
        branch.save()

    SiteSettings.objects.update(
        meta_description_ar=(
            "عسل نحل الريف: عسل ومنتجات النحل من الفرع الرئيسي في طنطا، محافظة الغربية."
        )
    )

    Page.objects.filter(slug="about").update(
        meta_description=(
            "عسل نحل الريف: شركة الريف للمواد الغذائية، والفرع الرئيسي في طنطا، "
            "محافظة الغربية."
        )
    )
    Page.objects.filter(slug="contact").update(
        subtitle_ar=(
            "أسرع طريقة: اتصال أو واتساب على رقم الفرع الرئيسي. "
            "أو ابعتلنا سؤالك من هنا واحنا نرجعلك."
        ),
        meta_description=(
            "رقم وعنوان الفرع الرئيسي لعسل نحل الريف في طنطا، محافظة الغربية، "
            "ونموذج للأسئلة عن المنتجات والطلبات."
        ),
    )

    PageSection.objects.filter(page__slug="about", key="who-we-are").update(
        title="فرع حقيقي، مش مجرد صفحة على الإنترنت",
        body=(
            "الفرع الرئيسي على طريق طنطا المحلة أمام محطة قطار حصة شبشير، وتقدر تعدّي "
            "عليه في مواعيد العمل. الموقع ده امتداد للفرع الرئيسي، مش بديل عنه: نفس "
            "المنتجات اللي على الرف، بنفس الأسماء المكتوبة على العبوات."
        ),
    )
    PageSection.objects.filter(page__slug="about", key="value-response").update(
        body="سؤال قبل الطلب أو بعده — في حد بيرد على رقم الفرع الرئيسي."
    )

    FaqItem.objects.filter(question="أقدر أشتري من المحل؟").update(
        question="أقدر أشتري من الفرع الرئيسي؟",
        answer=f"أيوة. {BRANCH_NAME} على {BRANCH_ADDRESS}.",
        topic=BRANCH_NAME,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0002_remove_unverified_founding_year"),
    ]

    operations = [
        migrations.AlterField(
            model_name="contactmessage",
            name="phone",
            field=models.CharField(
                max_length=15,
                validators=[
                    django.core.validators.RegexValidator(
                        message="Enter an Egyptian mobile number, e.g. 01012345678.",
                        regex="^01[0125]\\d{8}$",
                    )
                ],
            ),
        ),
        migrations.AlterField(
            model_name="contactphone",
            name="dial",
            field=models.CharField(
                help_text="e.g. 201001234567",
                max_length=20,
                validators=[
                    django.core.validators.RegexValidator(
                        message="Digits only, country code first, e.g. 201001234567.",
                        regex="^\\d{8,15}$",
                    )
                ],
            ),
        ),
        migrations.AlterField(
            model_name="contactphone",
            name="display",
            field=models.CharField(
                help_text="As printed, e.g. +20 10 01234567.", max_length=40
            ),
        ),
        migrations.RunPython(update_verified_contact_details, migrations.RunPython.noop),
    ]
