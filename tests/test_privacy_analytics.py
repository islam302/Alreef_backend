"""The privacy policy discloses Google Analytics instead of claiming no third-party tracking."""

import pytest

from apps.content.models import Page, PageSection
from apps.content.policy_pages import ANALYTICS_REWRITES, disclose_analytics

pytestmark = pytest.mark.django_db


def _policy(*bodies):
    page = Page.objects.filter(slug="privacy-policy").first() or Page.objects.create(slug="privacy-policy", title_ar="سياسة الخصوصية")
    PageSection.objects.filter(page=page).delete()
    for i, body in enumerate(bodies):
        PageSection.objects.create(page=page, kind="text", title=f"s{i}", body=body, sort_order=i)
    return page


def test_published_wording_is_swapped():
    (old1, new1), (old2, new2) = ANALYTICS_REWRITES
    page = _policy(f"مقدمة. {old1}", f"{old2}")
    assert disclose_analytics(Page, PageSection) == 2
    bodies = list(page.sections.order_by("sort_order").values_list("body", flat=True))
    assert bodies == [f"مقدمة. {new1}", new2]
    assert disclose_analytics(Page, PageSection) == 0  # idempotent


def test_edited_policy_is_left_alone():
    page = _policy("نص كتبه الفريق بنفسه.")
    assert disclose_analytics(Page, PageSection) == 0
    assert page.sections.get().body == "نص كتبه الفريق بنفسه."
