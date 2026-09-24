"""
Tests for `apps.content` — the editable site content (settings, phones,
branches, social links, pages, FAQ) and the contact-form inbox.

Two boundaries matter here:

1. The public endpoints are read-only and anonymous. The only write a
   visitor can perform is submitting the contact form, and that write must
   not let them set staff fields (status, notes) on the row.
2. Every management route is staff-only, exactly like the rest of
   /api/v1/admin-api/, and unpublished pages stay invisible publicly.

Requires Postgres at localhost:5433 (see docker-compose.yml).
"""
import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.audit.models import AuditLog
from apps.content.models import (
    Branch,
    ContactMessage,
    ContactMessageStatus,
    ContactPhone,
    FaqItem,
    Page,
    PageSection,
    SiteSettings,
    SocialLink,
    SocialPlatform,
)
from apps.users.models import User

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def staff_user(db):
    return User.objects.create_user(
        email="content-staff@alreef.com", password="StaffSecret123",
        first_name="Mona", last_name="Saeed", is_staff=True,
    )


@pytest.fixture
def staff_client(staff_user):
    client = APIClient()
    refresh = RefreshToken.for_user(staff_user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


@pytest.fixture
def site_content(db):
    settings_row = SiteSettings.load()
    settings_row.name_ar = "الريف"
    settings_row.tagline_ar = "الريف، على سفرتك"
    settings_row.founded_year = 2005
    settings_row.save()

    ContactPhone.objects.create(display="010 6060 4405", dial="201060604405", is_whatsapp=True, is_primary=True)
    ContactPhone.objects.create(display="015 0585 8198", dial="201505858198", sort_order=1)
    ContactPhone.objects.create(display="010 0000 0000", dial="201000000000", is_active=False, sort_order=2)
    Branch.objects.create(name="المحل", address="شبشير الحصة، طنطا", region="الغربية")
    SocialLink.objects.create(platform=SocialPlatform.FACEBOOK, label="فيسبوك", url="https://facebook.com/AlreeForHony")
    return settings_row


@pytest.fixture
def about_page(db):
    page = Page.objects.create(slug="about", title_ar="عن الريف", body_ar="نص الصفحة")
    PageSection.objects.create(page=page, key="values", title="الوضوح", body="نص", sort_order=0)
    PageSection.objects.create(page=page, key="hidden", title="مخفي", is_active=False, sort_order=1)
    return page


# --------------------------------------------------------------------------
# Public reads
# --------------------------------------------------------------------------
def test_site_bundle_is_public_and_hides_inactive_rows(api_client, site_content):
    response = api_client.get("/api/v1/content/site/")

    assert response.status_code == 200
    data = response.data["data"]
    assert data["settings"]["name_ar"] == "الريف"
    assert data["settings"]["founded_year"] == 2005
    assert [p["dial"] for p in data["phones"]] == ["201060604405", "201505858198"]
    assert data["branches"][0]["region"] == "الغربية"
    assert data["social"][0]["platform"] == "facebook"


def test_site_bundle_works_on_an_empty_database(api_client):
    """A fresh install must not 404 the storefront header."""
    response = api_client.get("/api/v1/content/site/")

    assert response.status_code == 200
    assert response.data["data"]["settings"]["name_ar"] == ""
    assert response.data["data"]["phones"] == []


def test_page_detail_returns_active_sections_only(api_client, about_page):
    response = api_client.get("/api/v1/content/pages/about/")

    assert response.status_code == 200
    data = response.data["data"]
    assert data["title_ar"] == "عن الريف"
    assert [s["key"] for s in data["sections"]] == ["values"]


def test_unpublished_page_is_invisible_publicly(api_client, about_page):
    about_page.is_published = False
    about_page.save()

    assert api_client.get("/api/v1/content/pages/about/").status_code == 404
    assert api_client.get("/api/v1/content/pages/").data["data"] == []


def test_faq_list_is_ordered_and_skips_inactive(api_client):
    FaqItem.objects.create(question="تاني", answer="نص", sort_order=1)
    FaqItem.objects.create(question="أول", answer="نص", sort_order=0)
    FaqItem.objects.create(question="مخفي", answer="نص", sort_order=2, is_active=False)

    response = api_client.get("/api/v1/content/faqs/")

    assert response.status_code == 200
    assert [f["question"] for f in response.data["data"]] == ["أول", "تاني"]


# --------------------------------------------------------------------------
# Contact form
# --------------------------------------------------------------------------
def test_anonymous_visitor_can_submit_the_contact_form(api_client):
    response = api_client.post(
        "/api/v1/content/contact-messages/",
        {"name": "أحمد علي", "phone": "01060604405", "subject": "سؤال", "message": "عايز أعرف مواعيد التسليم."},
        format="json",
    )

    assert response.status_code == 201
    message = ContactMessage.objects.get()
    assert message.status == ContactMessageStatus.NEW
    assert message.name == "أحمد علي"


@pytest.mark.parametrize(
    "typed,stored",
    [
        ("01060604405", "01060604405"),
        ("010 6060 4405", "01060604405"),
        ("010-6060-4405", "01060604405"),
        ("+201060604405", "01060604405"),
        ("201060604405", "01060604405"),
    ],
)
def test_contact_form_normalises_the_way_people_type_a_number(api_client, typed, stored):
    response = api_client.post(
        "/api/v1/content/contact-messages/",
        {"name": "أحمد علي", "phone": typed, "message": "رسالة كافية للاختبار."},
        format="json",
    )

    assert response.status_code == 201, response.data
    assert ContactMessage.objects.get().phone == stored


def test_contact_form_rejects_a_bad_phone_and_a_too_short_message(api_client):
    response = api_client.post(
        "/api/v1/content/contact-messages/",
        {"name": "أحمد", "phone": "12345", "message": "قصير"},
        format="json",
    )

    assert response.status_code == 400
    assert not ContactMessage.objects.exists()


def test_contact_form_cannot_set_staff_fields(api_client):
    api_client.post(
        "/api/v1/content/contact-messages/",
        {
            "name": "أحمد علي", "phone": "01060604405", "message": "رسالة كافية للاختبار.",
            "status": "replied", "staff_note": "injected",
        },
        format="json",
    )

    message = ContactMessage.objects.get()
    assert message.status == ContactMessageStatus.NEW
    assert message.staff_note == ""


def test_contact_form_is_throttled(api_client):
    payload = {"name": "أحمد علي", "phone": "01060604405", "message": "رسالة كافية للاختبار."}
    statuses = [
        api_client.post("/api/v1/content/contact-messages/", payload, format="json").status_code
        for _ in range(7)
    ]

    assert 429 in statuses


# --------------------------------------------------------------------------
# Permission boundary
# --------------------------------------------------------------------------
ADMIN_ROUTES = [
    "/api/v1/admin-api/site-settings/",
    "/api/v1/admin-api/contact-phones/",
    "/api/v1/admin-api/branches/",
    "/api/v1/admin-api/social-links/",
    "/api/v1/admin-api/pages/",
    "/api/v1/admin-api/page-sections/",
    "/api/v1/admin-api/faqs/",
    "/api/v1/admin-api/contact-messages/",
]


@pytest.mark.parametrize("route", ADMIN_ROUTES)
def test_admin_content_routes_reject_anonymous(api_client, route):
    assert api_client.get(route).status_code in (401, 403)


@pytest.mark.parametrize("route", ADMIN_ROUTES)
def test_admin_content_routes_reject_customers(authenticated_client, route):
    assert authenticated_client.get(route).status_code == 403


def test_customer_cannot_read_the_contact_inbox(authenticated_client):
    ContactMessage.objects.create(name="أحمد", phone="01060604405", message="رسالة")

    assert authenticated_client.get("/api/v1/admin-api/contact-messages/").status_code == 403


# --------------------------------------------------------------------------
# Staff management
# --------------------------------------------------------------------------
def test_staff_can_edit_site_settings_and_the_change_is_public(staff_client, api_client):
    response = staff_client.patch(
        "/api/v1/admin-api/site-settings/", {"tagline_ar": "طعم البيت"}, format="json"
    )

    assert response.status_code == 200
    assert api_client.get("/api/v1/content/site/").data["data"]["settings"]["tagline_ar"] == "طعم البيت"
    assert AuditLog.objects.filter(model_name="SiteSettings").exists()


def test_site_settings_stays_a_singleton(staff_client):
    staff_client.patch("/api/v1/admin-api/site-settings/", {"name_ar": "الريف"}, format="json")
    staff_client.patch("/api/v1/admin-api/site-settings/", {"name_en": "El Rif"}, format="json")

    assert SiteSettings.objects.count() == 1


def test_staff_can_create_a_phone_and_only_one_stays_primary(staff_client):
    first = staff_client.post(
        "/api/v1/admin-api/contact-phones/",
        {"display": "010 6060 4405", "dial": "201060604405", "is_primary": True},
        format="json",
    )
    assert first.status_code == 201

    staff_client.post(
        "/api/v1/admin-api/contact-phones/",
        {"display": "015 0585 8198", "dial": "201505858198", "is_primary": True},
        format="json",
    )

    assert ContactPhone.objects.filter(is_primary=True).count() == 1
    assert ContactPhone.objects.get(is_primary=True).dial == "201505858198"


def test_phone_dial_must_be_digits(staff_client):
    response = staff_client.post(
        "/api/v1/admin-api/contact-phones/",
        {"display": "010 6060 4405", "dial": "+20 (106) 060-4405"},
        format="json",
    )

    assert response.status_code == 400


def test_staff_can_reorder_faqs_in_one_request(staff_client):
    first = FaqItem.objects.create(question="أول", answer="نص", sort_order=0)
    second = FaqItem.objects.create(question="تاني", answer="نص", sort_order=1)

    response = staff_client.post(
        "/api/v1/admin-api/faqs/reorder/",
        {"items": [{"id": first.id, "sort_order": 1}, {"id": second.id, "sort_order": 0}]},
        format="json",
    )

    assert response.status_code == 200
    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.sort_order, second.sort_order) == (1, 0)


def test_staff_can_add_a_section_to_a_page(staff_client, about_page):
    response = staff_client.post(
        f"/api/v1/admin-api/pages/{about_page.id}/sections/",
        {"kind": "card", "key": "value-response", "title": "الرد", "body": "في حد بيرد.", "sort_order": 2},
        format="json",
    )

    assert response.status_code == 201
    assert about_page.sections.filter(key="value-response").exists()


def test_admin_page_list_keeps_display_order(staff_client, about_page):
    Page.objects.create(slug="shipping", title_ar="الشحن", sort_order=2)
    Page.objects.create(slug="contact", title_ar="تواصل", sort_order=1)

    response = staff_client.get("/api/v1/admin-api/pages/")

    assert [p["slug"] for p in response.data["data"]] == ["about", "contact", "shipping"]
    assert response.data["data"][0]["section_count"] == 2


def test_page_reorder_is_not_swallowed_by_the_slug_lookup(staff_client, about_page):
    """`pages/` accepts a slug in the detail route, so `pages/reorder/` must
    still reach the bulk action rather than 404 as a missing page."""
    other = Page.objects.create(slug="shipping", title_ar="الشحن", sort_order=1)

    response = staff_client.post(
        "/api/v1/admin-api/pages/reorder/",
        {"items": [{"id": about_page.id, "sort_order": 1}, {"id": other.id, "sort_order": 0}]},
        format="json",
    )

    assert response.status_code == 200
    about_page.refresh_from_db()
    assert about_page.sort_order == 1


def test_page_can_be_fetched_by_slug_from_the_dashboard(staff_client, about_page):
    response = staff_client.get("/api/v1/admin-api/pages/about/")

    assert response.status_code == 200
    assert response.data["data"]["slug"] == "about"


def test_staff_cannot_store_script_in_page_copy(staff_client, about_page):
    response = staff_client.patch(
        f"/api/v1/admin-api/pages/{about_page.id}/",
        {"body_ar": "<script>alert(1)</script>"},
        format="json",
    )

    assert response.status_code == 400
    about_page.refresh_from_db()
    assert about_page.body_ar == "نص الصفحة"


def test_triaging_a_message_records_who_handled_it(staff_client, staff_user):
    message = ContactMessage.objects.create(name="أحمد", phone="01060604405", message="رسالة")

    response = staff_client.patch(
        f"/api/v1/admin-api/contact-messages/{message.id}/",
        {"status": "replied", "staff_note": "اتكلمنا معاه."},
        format="json",
    )

    assert response.status_code == 200
    message.refresh_from_db()
    assert message.status == ContactMessageStatus.REPLIED
    assert message.handled_by == staff_user
    assert message.handled_at is not None


def test_staff_cannot_rewrite_a_customer_message(staff_client):
    message = ContactMessage.objects.create(name="أحمد", phone="01060604405", message="الرسالة الأصلية")

    staff_client.patch(
        f"/api/v1/admin-api/contact-messages/{message.id}/", {"message": "محرَّفة"}, format="json"
    )

    message.refresh_from_db()
    assert message.message == "الرسالة الأصلية"


def test_unread_count_reports_new_messages(staff_client):
    ContactMessage.objects.create(name="أ", phone="01060604405", message="١")
    ContactMessage.objects.create(name="ب", phone="01060604405", message="٢", status=ContactMessageStatus.READ)

    response = staff_client.get("/api/v1/admin-api/contact-messages/unread-count/")

    assert response.status_code == 200
    assert response.data["data"]["count"] == 1


def test_seed_content_is_idempotent(db):
    from django.core.management import call_command

    call_command("seed_content")
    call_command("seed_content")

    assert SiteSettings.objects.count() == 1
    assert ContactPhone.objects.count() == 2
    assert Page.objects.filter(slug="about").count() == 1
    assert FaqItem.objects.count() == 8
