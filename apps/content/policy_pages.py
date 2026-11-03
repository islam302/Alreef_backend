"""
The shop's three published policies: privacy, refund & cancellation, delivery.

Written from what the system actually does (cancellation until the order ships,
the 30-day window for defective or spoiled products in ReturnPolicySettings, no
change-of-mind returns on food, refunds to the original method, fees computed
per governorate) and from the verified business facts —
nothing here is a promise the code does not keep. Egyptian law they follow:
Consumer Protection Law 181/2018 and Personal Data Protection Law 151/2020.

Used twice, on purpose: migration 0005 creates them on a running shop (only
where the slug does not exist yet, so an edit made in the dashboard is never
overwritten), and `seed_content` creates them on a fresh installation. Once
created they belong to the dashboard — edit them there, not here.
"""

from django.db.models import F

BUSINESS = "شركة الريف للمواد الغذائية (عسل نحل الريف)"
ADDRESS = "طريق طنطا–المحلة أمام محطة قطار حصة شبشير، طنطا، محافظة الغربية، مصر"
PHONE = "+20 15 54112325"
SITE = "www.alref.net"

#: Stands in for the contact section; filled in when the pages are created,
#: with the email the shop has in its site settings at that moment.
_CONTACT = "{contact}"


def contact_block(email=""):
    # The number is isolated left-to-right (LRI … PDI): inside an Arabic line
    # the bidi algorithm would otherwise print "+20 15 54112325" backwards.
    lines = [BUSINESS, f"العنوان: {ADDRESS}", f"تليفون وواتساب: ⁦{PHONE}⁩"]
    if email:
        lines.append(f"البريد الإلكتروني: {email}")
    lines.append("أو من صفحة «تواصل معنا» على الموقع.")
    return "\n".join(lines)

PRIVACY = {
    "slug": "privacy-policy",
    "title_ar": "سياسة الخصوصية",
    "title_en": "Privacy Policy",
    "subtitle_ar": "إيه البيانات اللي بنجمعها لما تطلب من الريف، وبنستخدمها في إيه، ومين بيشوفها، وإزاي تتحكم فيها.",
    "meta_description": "سياسة الخصوصية لمتجر عسل نحل الريف: البيانات اللي بنجمعها، استخدامها، مشاركتها مع شركات الشحن والدفع، وحقوقك طبقًا لقانون حماية البيانات الشخصية المصري.",
    "sort_order": 50,
    "sections": [
        ("مين إحنا", f"الموقع {SITE} تابع لـ{BUSINESS}، وفرعنا الرئيسي على {ADDRESS}.\n\nالسياسة دي بتوضح إزاي بنتعامل مع بياناتك الشخصية طبقًا لقانون حماية البيانات الشخصية المصري رقم 151 لسنة 2020."),
        ("البيانات اللي بنجمعها", "لما تطلب: اسمك، رقم موبايلك، عنوان التوصيل والمحافظة، والإيميل لو كتبته، وتفاصيل الطلب.\n\nلو عملت حساب: الإيميل وكلمة السر (محفوظة مشفّرة، ومحدش عندنا يقدر يقراها)، وعناوينك وطلباتك ومفضلتك.\n\nلو راسلتنا: الاسم والرقم والرسالة اللي بتبعتها من صفحة تواصل معنا.\n\nبيانات استخدام بسيطة: المنتجات اللي بتتفرج عليها وكلمات البحث وإضافات السلة، عشان نحسّن المتجر. دي بتتسجل على سيرفراتنا إحنا، ومش بنستخدم أدوات تتبع أو إعلانات من جهات خارجية."),
        ("بيانات الدفع", "الموقع مش بيشوف ولا بيحفظ بيانات بطاقتك البنكية. الدفع الإلكتروني بيتم على صفحة مزوّد خدمة الدفع المرخّص، وإحنا بيوصلنا بس نتيجة العملية (اتدفعت أو لأ) ورقمها المرجعي."),
        ("بنستخدم بياناتك في إيه", "• تجهيز طلبك وتوصيله والتواصل معاك بخصوصه.\n• تأكيد الطلب بالتليفون أو واتساب، وإرسال رسايل حالة الطلب على الإيميل.\n• الرد على أسئلتك وطلبات الاسترجاع.\n• حماية المتجر من الاحتيال وإساءة الاستخدام.\n• الالتزام بالقوانين، زي حفظ الفواتير.\n\nمش بنبيع بياناتك لأي حد، ومش بنبعتلك رسايل تسويقية من غير موافقتك."),
        ("مين بيشوف بياناتك", "بنشارك أقل قدر لازم من البيانات مع:\n• شركة الشحن: الاسم والموبايل والعنوان، عشان توصّلك الطلب.\n• مزوّد خدمة الدفع: بيانات العملية فقط، لو دفعت إلكترونيًا.\n• مزوّد البريد الإلكتروني: عشان رسايل الطلب توصلك.\n• مزوّد الاستضافة اللي شغّال عليه الموقع، وممكن تكون سيرفراته خارج مصر، مع إجراءات حماية مناسبة.\n• مساعد الريف (الشات): الأسئلة اللي بتكتبها فيه بتتبعت لمزوّد خدمة ذكاء اصطناعي عشان يرد عليك، فمتكتبش فيه بيانات شخصية زي رقم بطاقة أو كلمة سر.\n\nوممكن نفصح عن بيانات لو جهة رسمية طلبتها بشكل قانوني."),
        ("الكوكيز والتخزين في المتصفح", "بنستخدم كوكي ضروري عشان سلتك تفضل محفوظة، وبنحفظ في متصفحك بيانات الدخول لو سجّلت دخول، ومحادثة الشات لحد ما تقفل التاب. مفيش كوكيز إعلانية."),
        ("بنحتفظ بالبيانات لحد إمتى", "بيانات الطلبات والفواتير بنحتفظ بيها المدة اللي يطلبها القانون للحسابات والضرايب. باقي البيانات بنحتفظ بيها طول ما حسابك موجود، وبنمسحها لو طلبت ده، إلا اللي القانون يلزمنا نحتفظ بيه."),
        ("حقوقك", "من حقك تعرف البيانات اللي عندنا عنك، وتطلب تصحيحها أو تحديثها، أو مسحها، أو تعترض على استخدامها في التسويق في أي وقت. ابعتلنا طلبك على الإيميل أو الواتساب وهنرد عليك في أقرب وقت."),
        ("حماية البيانات", "الموقع شغّال بالكامل على اتصال مشفّر (HTTPS)، وكلمات السر محفوظة مشفّرة، والوصول للوحة التحكم مقصور على فريق المتجر."),
        ("تعديل السياسة", "ممكن نحدّث السياسة دي من وقت للتاني، وتاريخ آخر تحديث بيظهر في الصفحة. استمرارك في استخدام الموقع بعد التحديث معناه موافقتك عليه."),
        ("تواصل معانا", _CONTACT),
    ],
}

#: Added to the refund policy when COD deposits were introduced (migration 0006
#: appends it to a policy that already exists).
DEPOSIT_SECTION = (
    "دفعة تأكيد الطلب في الدفع عند الاستلام",
    "لو اخترت الدفع عند الاستلام، بنطلب دفعة تأكيد بتحوّلها فودافون كاش أو إنستاباي عشان نأكد الطلب. المبلغ بيظهرلك قبل ما تأكد، "
    "وبيتخصم من إجمالي الطلب — يعني بتدفع الباقي بس عند الاستلام، ومفيش أي مبلغ زيادة.\n\n"
    "• لو لغيت الطلب قبل ما يتشحن، أو اتلغى من عندنا: بترجعلك كاملة بنفس الطريقة اللي حوّلت بيها خلال 7 أيام.\n"
    "• لو المنتج رجع بسبب عيب أو فساد: بترجع مع باقي المبلغ.\n"
    "• لو الطلب اتشحن ورجعلنا من غير ما تستلمه: مابترجعش.",
)

#: The deposit section as migration 0007 left it (before the returned-undelivered rule).
DEPOSIT_SECTION_V2 = (
    "دفعة تأكيد الطلب في الدفع عند الاستلام",
    "لو اخترت الدفع عند الاستلام، ممكن نطلب دفعة تأكيد بسيطة بتحوّلها فودافون كاش أو إنستاباي عشان نأكد الطلب. المبلغ بيظهرلك قبل ما تأكد، "
    "وبيتخصم من إجمالي الطلب — يعني بتدفع الباقي بس عند الاستلام، ومفيش أي مبلغ زيادة.\n\n"
    "لو لغيت الطلب قبل ما يتشحن، أو اتلغى من عندنا، دفعة التأكيد بترجعلك كاملة بنفس الطريقة اللي حوّلت بيها خلال 7 أيام. "
    "ولو المنتج رجع حسب السياسة دي، دفعة التأكيد بترجع مع باقي المبلغ.",
)

#: The section as migration 0006 first wrote it, before "عربون" became "دفعة
#: التأكيد". Migration 0007 rewrites it only while it still reads exactly so.
DEPOSIT_SECTION_V1 = (
    "العربون في الدفع عند الاستلام",
    "لو اخترت الدفع عند الاستلام، ممكن نطلب عربون بسيط بتحوّله فودافون كاش أو إنستاباي عشان نأكد الطلب. المبلغ بيظهرلك قبل ما تأكد، "
    "وبيتخصم من إجمالي الطلب — يعني المندوب بياخد الباقي بس، ومفيش أي مبلغ زيادة.\n\n"
    "لو لغيت الطلب قبل ما يتشحن، أو اتلغى من عندنا، العربون بيرجعلك كامل بنفس الطريقة اللي حوّلت بيها خلال 7 أيام. "
    "ولو المنتج رجع حسب السياسة دي، العربون بيرجع مع باقي المبلغ.",
)

REFUND = {
    "slug": "refund-policy",
    "title_ar": "سياسة الاسترجاع والإلغاء",
    "title_en": "Refund & Cancellation Policy",
    "subtitle_ar": "إزاي تلغي طلبك، وإمتى تقدر ترجّع منتج، وإزاي ترجعلك فلوسك.",
    "meta_description": "سياسة الاسترجاع والإلغاء لمتجر عسل نحل الريف: إلغاء الطلب مجانًا قبل الشحن، الإرجاع خلال 30 يوم لو المنتج فيه عيب أو فساد، ورد المبلغ بنفس طريقة الدفع، طبقًا لقانون حماية المستهلك المصري.",
    "sort_order": 51,
    "sections": [
        ("باختصار", "• تقدر تلغي طلبك مجانًا في أي وقت قبل ما يتشحن.\n• لو المنتج فيه عيب أو فساد، من حقك ترجّعه خلال 30 يوم من استلامه، وبنستبدله أو نرجّعلك فلوسك كاملة ومصاريف الشحن علينا.\n• عشان دي منتجات غذائية، مفيش استرجاع لمنتج سليم لمجرد تغيير الرأي.\n• لو الطلب اتشحن ورجعلنا من غير ما تستلمه، دفعة تأكيد الطلب مابترجعش.\n• الفلوس بترجعلك بنفس طريقة الدفع.\n\nالسياسة دي ماشية مع قانون حماية المستهلك المصري رقم 181 لسنة 2018، ومش بتقلل من أي حق القانون بيديهولك."),
        ("إلغاء الطلب", "تقدر تلغي الطلب من غير أي مصاريف طول ما هو لسه ما اتشحنش: من «طلباتي» في حسابك، أو ابعتلنا رقم الطلب على الواتساب.\n\nلو كنت دافع إلكترونيًا أو محوّل دفعة تأكيد، المبلغ كله بيرجعلك.\n\nبعد ما الطلب يتشحن مش بيتلغي."),
        ("حق الإرجاع في حال وجود عيب أو فساد (خلال 30 يوماً)", "من حقك ترجّع المنتج خلال 30 يوم من تاريخ استلامه لو:\n• وصلك مكسور، أو العبوة مفتوحة أو ختمها مش سليم.\n• منتج غير اللي طلبته، أو الطلب ناقص.\n• منتهي الصلاحية، أو فيه فساد أو عيب في الجودة.\n\nابعتلنا رقم الطلب وصورة للمنتج والعبوة. بعد ما نراجع، بنبعتلك بديل أو نرجّعلك المبلغ كامل حسب اختيارك، ومصاريف الشحن والإرجاع في الحالة دي علينا.\n\nولو الشحنة وصلت مكسورة، ياريت تصوّرها وتبلّغنا يوم الاستلام."),
        ("ليه مفيش استرجاع لو غيّرت رأيك", "العسل ومنتجات النحل منتجات غذائية. عشان صحتك وصحة كل عملائنا، مش بنرجّع منتج سليم لمجرد تغيير الرأي بعد ما يتسلم: مش بنقدر نتأكد إزاي اتخزّن، ومش هنبيعه لحد تاني.\n\nلو محتار تختار إيه، كلّمنا قبل ما تطلب وهنساعدك."),
        ("لو الطلب رجع من غير ما تستلمه", "لو الطلب اتشحن ورجعلنا من شركة الشحن من غير ما يتسلم — رفضت الاستلام، أو المندوب ماقدرش يوصلك بعد محاولات التواصل، أو العنوان أو الرقم مش صح — دفعة تأكيد الطلب مابترجعش، لأنها بتغطي مصاريف الشحن والرجوع.\n\nالطلب ده بيتقفل. لو لسه عايز المنتجات، اعمل طلب جديد من الموقع."),
        DEPOSIT_SECTION,
        ("إزاي تطلب استرجاع", "خلال 30 يوم من الاستلام:\n• لو عندك حساب: من «طلباتي» افتح الطلب واختار «طلب استرجاع» واكتب المشكلة.\n• أو ابعتلنا على الواتساب أو الإيميل رقم الطلب والمشكلة وصورة المنتج.\n\nفريقنا بيراجع الطلب ويرد عليك بالخطوات وميعاد استلام المنتج."),
        ("رد الفلوس", "بنرجّع المبلغ خلال 7 أيام من استلامنا المنتج المرتجع أو موافقتنا على الطلب.\n\n• الدفع عند الاستلام: بنحوّلك المبلغ على محفظة إلكترونية أو إنستاباي أو حساب بنكي باسمك.\n• الدفع بالكارت: المبلغ بيرجع على نفس الكارت، وظهوره في حسابك ممكن ياخد وقت إضافي حسب البنك.\n• المحافظ الإلكترونية وإنستاباي: بيرجع على نفس المحفظة أو الحساب اللي دفعت منه.\n\nولو تحب، ممكن نحطلك المبلغ رصيد في حسابك تستخدمه في طلب جاي."),
        ("تواصل معانا", _CONTACT),
    ],
}

#: The refund policy as it was published before change-of-mind returns were
#: dropped (14 days) and the 30-day defect window and the returned-undelivered
#: rule were added. Migration 0008 rewrites a page that still reads exactly so.
REFUND_V2_META = "سياسة الاسترجاع والإلغاء لمتجر عسل نحل الريف: إلغاء الطلب قبل الشحن، الاسترجاع خلال 14 يوم من الاستلام، ورد المبلغ بنفس طريقة الدفع، طبقًا لقانون حماية المستهلك المصري."
REFUND_SECTIONS_V2 = (
        ("باختصار", "• تقدر تلغي طلبك مجانًا في أي وقت قبل ما يتشحن.\n• تقدر ترجّع المنتج خلال 14 يوم من استلامه.\n• لو المنتج وصلك مكسور أو غلط أو فيه عيب، بنستبدله أو نرجّعلك فلوسك كاملة ومصاريف الشحن علينا.\n• الفلوس بترجعلك بنفس طريقة الدفع.\n\nالسياسة دي ماشية مع قانون حماية المستهلك المصري رقم 181 لسنة 2018، ومش بتقلل من أي حق القانون بيديهولك."),
        ("إلغاء الطلب", "تقدر تلغي الطلب من غير أي مصاريف طول ما هو لسه ما اتشحنش: من «طلباتي» في حسابك، أو ابعتلنا رقم الطلب على الواتساب.\n\nلو كنت دافع إلكترونيًا، المبلغ كله بيرجعلك.\n\nبعد ما الطلب يتشحن مش بيتلغي، لكن تقدر تستلمه وتطلب استرجاعه حسب الشروط اللي تحت."),
        ("استرجاع منتج", "تقدر تطلب استرجاع أي منتج خلال 14 يوم من تاريخ الاستلام.\n\nعشان صحتك وسلامة الأكل: المنتج اللي اتفتح أو اتكسر ختمه أو استُخدم مش بيترجع، إلا لو كان فيه عيب. لازم يرجع في عبوته وتغليفه الأصلي."),
        ("لو المنتج وصلك فيه مشكلة", "لو الطلب وصلك مكسور، أو منتج غير اللي طلبته، أو ناقص، أو منتهي الصلاحية، أو فيه عيب في الجودة: ابعتلنا رقم الطلب وصورة للمنتج والعبوة في أسرع وقت بعد الاستلام.\n\nبعد ما نراجع، بنبعتلك بديل أو نرجّعلك المبلغ كامل حسب اختيارك، ومصاريف الشحن والإرجاع في الحالة دي علينا."),
        ("لو غيّرت رأيك", "لو المنتج سليم ومقفول وعايز ترجّعه، تقدر خلال الـ14 يوم. مصاريف شحن الإرجاع في الحالة دي على العميل، وبنرجّعلك تمن المنتج بعد ما يوصلنا ونتأكد إنه بحالته."),
        DEPOSIT_SECTION_V2,
        ("إزاي تطلب استرجاع", "• لو عندك حساب: من «طلباتي» افتح الطلب واختار «طلب استرجاع» واكتب السبب.\n• أو ابعتلنا على الواتساب أو الإيميل رقم الطلب وسبب الاسترجاع وصورة المنتج.\n\nفريقنا بيراجع الطلب ويرد عليك بالخطوات وميعاد استلام المنتج."),
        ("رد الفلوس", "بنرجّع المبلغ خلال 7 أيام من استلامنا المنتج المرتجع أو موافقتنا على الطلب.\n\n• الدفع عند الاستلام: بنحوّلك المبلغ على محفظة إلكترونية أو إنستاباي أو حساب بنكي باسمك.\n• الدفع بالكارت: المبلغ بيرجع على نفس الكارت، وظهوره في حسابك ممكن ياخد وقت إضافي حسب البنك.\n• المحافظ الإلكترونية وإنستاباي: بيرجع على نفس المحفظة أو الحساب اللي دفعت منه.\n\nولو تحب، ممكن نحطلك المبلغ رصيد في حسابك تستخدمه في طلب جاي."),
        ("تواصل معانا", _CONTACT),
)

SHIPPING = {
    "slug": "shipping-policy",
    "title_ar": "سياسة الشحن والتوصيل",
    "title_en": "Delivery & Shipping Policy",
    "subtitle_ar": "بنوصل لفين، بكام، وفي قد إيه — وإيه اللي يحصل لو في مشكلة في التوصيل.",
    "meta_description": "سياسة الشحن والتوصيل لمتجر عسل نحل الريف: توصيل لكل محافظات مصر، مصاريف شحن بتظهر قبل تأكيد الطلب، ومدة توصيل متوقعة من 2 لـ 4 أيام عمل.",
    "sort_order": 52,
    "sections": [
        ("بنوصل لفين", "بنشحن لكل محافظات جمهورية مصر العربية. حاليًا مفيش شحن لخارج مصر."),
        ("مصاريف الشحن", "مصاريف الشحن بتتحسب حسب المحافظة، وبتظهرلك قبل ما تأكد الطلب — في حاسبة الشحن في صفحة «الشحن والدفع» وفي صفحة إتمام الطلب. ممكن تقل أو تبقى مجانية حسب قيمة الطلب والعروض السارية.\n\nلو اخترت الدفع عند الاستلام، في رسوم تحصيل بسيطة بتظهر قبل التأكيد.\n\nكل الأسعار ومصاريف الشحن بالجنيه المصري."),
        ("مدة التوصيل", "بعد ما بنأكد الطلب معاك بالتليفون، التوصيل بياخد عادة من 2 لـ 4 أيام عمل، حسب المحافظة وشركة الشحن. المدة المتوقعة لكل طريقة شحن مكتوبة جنبها وقت الطلب.\n\nممكن يحصل تأخير في المواسم والإجازات الرسمية أو الظروف اللي برا إيدينا، وساعتها بنبلّغك."),
        ("تأكيد الطلب وتجهيزه", "بعد ما تطلب، بنتواصل معاك نأكد الطلب والعنوان. العبوات الزجاج بتتراجع وتتغلف كويس قبل الشحن عشان توصل سليمة."),
        ("متابعة الطلب", "تقدر تتابع حالة طلبك في أي وقت من صفحة «تتبع طلبك» برقم الطلب ورقم الموبايل، أو من «طلباتي» لو عندك حساب."),
        ("وقت الاستلام", "ياريت تبص على الشحنة وقت الاستلام. لو العبوة مكسورة أو المنتج مختلف عن طلبك، صوّرها وابعتلنا رقم الطلب في أسرع وقت، وهنتعامل معاها حسب سياسة الاسترجاع والإلغاء.\n\nلو مندوب الشحن ما قدرش يوصلك، بنتواصل معاك نحدد ميعاد تاني."),
        ("تواصل معانا", _CONTACT),
    ],
}

POLICY_PAGES = (PRIVACY, REFUND, SHIPPING)


def add_deposit_section(Page, PageSection):
    """Append the deposit rules to an existing refund policy, once. Returns True if added."""
    page = Page.objects.filter(slug="refund-policy").first()
    title, body = DEPOSIT_SECTION
    if page is None or page.sections.filter(title=title).exists():
        return False
    # Before "how to request a return" when it is still there; otherwise last.
    anchor = page.sections.filter(title="إزاي تطلب استرجاع").first()
    if anchor is not None:
        page.sections.filter(sort_order__gte=anchor.sort_order).update(sort_order=F("sort_order") + 1)
        order = anchor.sort_order
    else:
        last = page.sections.order_by("-sort_order").first()
        order = (last.sort_order + 1) if last else 0
    PageSection.objects.create(page=page, kind="text", title=title, body=body, sort_order=order, is_active=True)
    return True


def rename_deposit_section(Page, PageSection):
    """
    Rewrite the first wording of the deposit section in the current one.

    Only a section that still reads exactly as first written is touched: one
    the team has edited from the dashboard is theirs and is left alone.
    Returns True if it was rewritten.
    """
    old_title, old_body = DEPOSIT_SECTION_V1
    title, body = DEPOSIT_SECTION_V2
    return bool(
        PageSection.objects.filter(page__slug="refund-policy", title=old_title, body=old_body).update(title=title, body=body)
    )


def rewrite_refund_policy_v3(Page, PageSection):
    """
    Bring a refund policy that still reads exactly as published (REFUND_SECTIONS_V2)
    to the current text. A page the team has edited in any section is left alone.
    The contact section keeps whatever it says. Returns True if rewritten.
    """
    page = Page.objects.filter(slug="refund-policy").first()
    if page is None:
        return False
    sections = list(page.sections.order_by("sort_order", "id"))
    current = [(x.title, x.body) for x in sections]
    expected = list(REFUND_SECTIONS_V2)
    if len(current) != len(expected):
        return False
    for (title, body), (want_title, want_body) in zip(current, expected):
        if title != want_title or (want_body != _CONTACT and body != want_body):
            return False

    contact = next(x for x in sections if x.title == "تواصل معانا")
    page.sections.exclude(pk=contact.pk).delete()
    for i, (title, body) in enumerate(REFUND["sections"]):
        if body == _CONTACT:
            PageSection.objects.filter(pk=contact.pk).update(sort_order=i)
            continue
        PageSection.objects.create(page=page, kind="text", title=title, body=body, sort_order=i, is_active=True)
    if page.meta_description == REFUND_V2_META:
        Page.objects.filter(pk=page.pk).update(meta_description=REFUND["meta_description"])
    return True


def create_missing(Page, PageSection, email=""):
    """
    Create each policy page that does not exist yet. Returns the slugs created.

    `email` is the shop's address from its site settings, written into each
    page's contact section; the phone and address are the verified facts above.
    """
    created = []
    for spec in POLICY_PAGES:
        if Page.objects.filter(slug=spec["slug"]).exists():
            continue
        page = Page.objects.create(
            slug=spec["slug"],
            title_ar=spec["title_ar"],
            title_en=spec["title_en"],
            subtitle_ar=spec["subtitle_ar"],
            meta_title=f"{spec['title_ar']} | عسل نحل الريف",
            meta_description=spec["meta_description"],
            is_published=True,
            sort_order=spec["sort_order"],
        )
        for i, (title, body) in enumerate(spec["sections"]):
            body = contact_block(email) if body == _CONTACT else body
            PageSection.objects.create(page=page, kind="text", title=title, body=body, sort_order=i, is_active=True)
        created.append(spec["slug"])
    return created
