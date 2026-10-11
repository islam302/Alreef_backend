"""
The homepage product sections (الأكثر مبيعًا، أنواع العسل، الخلطات).

The dashboard stores each as a section of the `home` page with its settings in
`data`, sent as JSON; the storefront reads them from the public page endpoint
and then asks the catalogue with the `badge` / `category` / `slugs` filters.
These tests pin that round trip, so a change to the content API cannot quietly
break what the shop controls from the dashboard.
"""

import pytest

pytestmark = pytest.mark.django_db

ADMIN_PAGES = "/api/v1/admin-api/pages/"
ADMIN_SECTIONS = "/api/v1/admin-api/page-sections/"
PUBLIC_HOME = "/api/v1/content/pages/home/"


def _home(staff_client):
    response = staff_client.post(
        ADMIN_PAGES, {"slug": "home", "title_ar": "الصفحة الرئيسية", "is_published": True}, format="json"
    )
    assert response.status_code == 201, response.json()
    return response.json()["data"]["id"]


def _section(**overrides):
    body = {
        "kind": "card",
        "key": "best-sellers",
        "eyebrow": "اختيارات عليها طلب",
        "title": "الأكثر مبيعًا",
        "body": "المنتجات اللي عملاء الريف بيرجعوا يطلبوها.",
        "sort_order": 1,
        "is_active": True,
        "data": {"show": True, "source": "badge", "category": "", "products": [], "limit": 8},
    }
    body.update(overrides)
    return body


def test_staff_saves_a_section_as_json_and_the_storefront_reads_it(staff_client, api_client):
    page_id = _home(staff_client)

    created = staff_client.post(f"{ADMIN_PAGES}{page_id}/sections/", _section(), format="json")
    assert created.status_code == 201, created.json()

    sections = api_client.get(PUBLIC_HOME).json()["data"]["sections"]
    [section] = [s for s in sections if s["key"] == "best-sellers"]
    assert section["title"] == "الأكثر مبيعًا"
    assert section["data"] == {"show": True, "source": "badge", "category": "", "products": [], "limit": 8}


def test_staff_switches_the_source_to_hand_picked_products(staff_client, api_client):
    page_id = _home(staff_client)
    section_id = staff_client.post(
        f"{ADMIN_PAGES}{page_id}/sections/", _section(key="mixtures"), format="json"
    ).json()["data"]["id"]

    picked = {"show": True, "source": "manual", "category": "", "products": ["b", "a"], "limit": 8}
    response = staff_client.patch(f"{ADMIN_SECTIONS}{section_id}/", {"data": picked, "sort_order": 3}, format="json")
    assert response.status_code == 200, response.json()

    [section] = [s for s in api_client.get(PUBLIC_HOME).json()["data"]["sections"] if s["key"] == "mixtures"]
    assert section["data"]["products"] == ["b", "a"]
    assert section["sort_order"] == 3


def test_a_hidden_section_is_still_served_so_it_is_not_mistaken_for_unconfigured(staff_client, api_client):
    """Hiding is data.show=false on an active section. An inactive section is
    dropped by the public endpoint, and the storefront would then fall back to
    its default and show the section the shop asked to hide."""
    page_id = _home(staff_client)
    hidden = _section(key="honey-types", data={"show": False, "source": "category", "category": "honey", "limit": 8})
    staff_client.post(f"{ADMIN_PAGES}{page_id}/sections/", hidden, format="json")

    [section] = [s for s in api_client.get(PUBLIC_HOME).json()["data"]["sections"] if s["key"] == "honey-types"]
    assert section["data"]["show"] is False


def test_customers_cannot_write_homepage_sections(api_client, staff_client):
    page_id = _home(staff_client)

    response = api_client.post(f"{ADMIN_PAGES}{page_id}/sections/", _section(), format="json")

    assert response.status_code in (401, 403)
