"""
The three policies a payment provider asks for before approving the shop:
privacy, refund & cancellation, delivery & shipping. They are created once and
then belong to the dashboard.
"""

import pytest

from apps.content.models import Page, PageSection, SiteSettings
from apps.content.policy_pages import POLICY_PAGES, create_missing

SLUGS = {"privacy-policy", "refund-policy", "shipping-policy"}


@pytest.mark.django_db
def test_all_three_policies_are_created_and_published():
    Page.objects.filter(slug__in=SLUGS).delete()
    created = create_missing(Page, PageSection, email="shop@example.com")

    assert set(created) == SLUGS
    for slug in SLUGS:
        page = Page.objects.get(slug=slug)
        assert page.is_published
        assert page.sections.filter(is_active=True).count() >= 5
        contact = page.sections.get(title="تواصل معانا").body
        assert "shop@example.com" in contact
        assert "⁦+20 15 54112325⁩" in contact  # isolated, so it reads left to right
        assert "طنطا، محافظة الغربية، مصر" in contact


@pytest.mark.django_db
def test_an_edited_policy_is_never_overwritten():
    Page.objects.filter(slug__in=SLUGS).delete()
    create_missing(Page, PageSection)
    Page.objects.filter(slug="refund-policy").update(title_ar="نسخة المحامي")

    assert create_missing(Page, PageSection) == []
    assert Page.objects.get(slug="refund-policy").title_ar == "نسخة المحامي"


@pytest.mark.django_db
def test_without_an_email_the_contact_section_still_reads():
    Page.objects.filter(slug__in=SLUGS).delete()
    create_missing(Page, PageSection, email="")
    contact = Page.objects.get(slug="privacy-policy").sections.get(title="تواصل معانا").body
    assert "البريد الإلكتروني" not in contact
    assert "{contact}" not in contact


@pytest.mark.django_db
def test_policies_are_public(api_client):
    Page.objects.filter(slug__in=SLUGS).delete()
    create_missing(Page, PageSection, email=SiteSettings.load().email)

    listed = {p["slug"] for p in api_client.get("/api/v1/content/pages/").json()["data"]}
    assert SLUGS <= listed
    res = api_client.get("/api/v1/content/pages/refund-policy/")
    assert res.status_code == 200
    body = " ".join(s["body"] for s in res.json()["data"]["sections"])
    assert "30 يوم" in body and "7 أيام" in body
    assert "14 يوم" not in body


def test_the_promises_match_the_system():
    """The refund policy states the window ReturnPolicySettings defaults to, and no change-of-mind returns."""
    from apps.orders.models import ReturnPolicySettings

    default = ReturnPolicySettings._meta.get_field("return_window_days").default
    refund = next(p for p in POLICY_PAGES if p["slug"] == "refund-policy")
    text = " ".join(body for _, body in refund["sections"])
    assert f"{default} يوم" in text
    assert not ReturnPolicySettings._meta.get_field("allow_change_of_mind_returns").default
    assert "مفيش استرجاع لمنتج سليم لمجرد تغيير الرأي" in text


@pytest.mark.django_db
def test_the_deposit_rules_are_added_to_an_existing_refund_policy_once():
    from apps.content.policy_pages import DEPOSIT_SECTION, add_deposit_section

    Page.objects.filter(slug__in=SLUGS).delete()
    create_missing(Page, PageSection)
    page = Page.objects.get(slug="refund-policy")
    page.sections.filter(title=DEPOSIT_SECTION[0]).delete()  # a policy created before deposits

    assert add_deposit_section(Page, PageSection) is True
    assert add_deposit_section(Page, PageSection) is False
    titles = list(page.sections.order_by("sort_order").values_list("title", flat=True))
    assert titles.count(DEPOSIT_SECTION[0]) == 1
    assert titles.index(DEPOSIT_SECTION[0]) == titles.index("إزاي تطلب استرجاع") - 1


@pytest.mark.django_db
def test_the_first_deposit_wording_is_rewritten_but_an_edited_one_is_kept():
    from apps.content.policy_pages import DEPOSIT_SECTION, DEPOSIT_SECTION_V1, DEPOSIT_SECTION_V2, rename_deposit_section

    Page.objects.filter(slug__in=SLUGS).delete()
    create_missing(Page, PageSection)
    section = PageSection.objects.get(page__slug="refund-policy", title=DEPOSIT_SECTION[0])

    section.title, section.body = DEPOSIT_SECTION_V1
    section.save()
    assert rename_deposit_section(Page, PageSection) is True
    section.refresh_from_db()
    assert (section.title, section.body) == DEPOSIT_SECTION_V2

    # Edited by the team: theirs, left alone.
    section.title, section.body = DEPOSIT_SECTION_V1[0], "نص كتبه الفريق"
    section.save()
    assert rename_deposit_section(Page, PageSection) is False
    section.refresh_from_db()
    assert section.body == "نص كتبه الفريق"



def _publish_v2():
    """The refund policy exactly as it was published before the food-returns rewrite."""
    from apps.content.policy_pages import REFUND_SECTIONS_V2, REFUND_V2_META, contact_block

    Page.objects.filter(slug__in=SLUGS).delete()
    create_missing(Page, PageSection)
    page = Page.objects.get(slug="refund-policy")
    page.sections.all().delete()
    page.meta_description = REFUND_V2_META
    page.save()
    for i, (title, body) in enumerate(REFUND_SECTIONS_V2):
        PageSection.objects.create(
            page=page, kind="text", title=title, sort_order=i, is_active=True,
            body=contact_block("shop@example.com") if body == "{contact}" else body,
        )
    return page


@pytest.mark.django_db
def test_the_published_refund_policy_is_rewritten_for_food():
    from apps.content.policy_pages import REFUND, rewrite_refund_policy_v3

    page = _publish_v2()
    assert rewrite_refund_policy_v3(Page, PageSection) is True
    sections = list(page.sections.order_by("sort_order"))
    assert [s.title for s in sections] == [t for t, _ in REFUND["sections"]]
    text = " ".join(s.body for s in sections)
    assert "خلال 14 يوم" not in text
    assert "دفعة تأكيد الطلب مابترجعش" in text and "اعمل طلب جديد" in text
    assert "shop@example.com" in sections[-1].body  # the contact section keeps what it said
    page.refresh_from_db()
    assert page.meta_description == REFUND["meta_description"]
    assert rewrite_refund_policy_v3(Page, PageSection) is False  # once


@pytest.mark.django_db
def test_an_edited_refund_policy_is_not_rewritten():
    from apps.content.policy_pages import rewrite_refund_policy_v3

    page = _publish_v2()
    page.sections.filter(title="رد الفلوس").update(body="نص كتبه المحامي")
    assert rewrite_refund_policy_v3(Page, PageSection) is False
    assert page.sections.get(title="رد الفلوس").body == "نص كتبه المحامي"
