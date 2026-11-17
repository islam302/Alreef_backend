"""
Chat screenshots: what customers wrote to the shop, uploaded by staff and
shown under «آراء عملائنا». Only staff may write, only published ones are
public, and the file must be a real image.
"""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.reviews.models import ChatScreenshot

pytestmark = pytest.mark.django_db

ADMIN = "/api/v1/admin-api/chat-screenshots/"
PUBLIC = "/api/v1/reviews/screenshots/"


def _png(name="chat.png"):
    buf = io.BytesIO()
    Image.new("RGB", (60, 120), (30, 30, 30)).save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def test_staff_uploads_a_screenshot_and_the_public_sees_it(staff_client, api_client):
    r = staff_client.post(ADMIN, {"image": _png(), "caption": "  عسل   الموالح "}, format="multipart")
    assert r.status_code == 201, r.content
    body = r.json()["data"]
    assert body["published"] is True
    assert body["caption"] == "عسل الموالح"

    data = api_client.get(PUBLIC).json()["data"]
    assert [s["id"] for s in data] == [body["id"]]
    assert data[0]["image"].endswith(".png")
    assert set(data[0]) == {"id", "image", "caption"}


def test_hidden_screenshots_are_not_public(staff_client, api_client):
    sid = staff_client.post(ADMIN, {"image": _png()}, format="multipart").json()["data"]["id"]
    assert staff_client.patch(ADMIN + f"{sid}/", {"published": False}, format="json").status_code == 200
    assert api_client.get(PUBLIC).json()["data"] == []
    assert len(staff_client.get(ADMIN).json()["data"]) == 1


def test_public_list_follows_sort_order(api_client):
    b = ChatScreenshot.objects.create(image=_png("b.png"), sort_order=2)
    a = ChatScreenshot.objects.create(image=_png("a.png"), sort_order=1)
    assert [s["id"] for s in api_client.get(PUBLIC).json()["data"]] == [a.id, b.id]


def test_customers_and_guests_cannot_write(api_client, authenticated_client):
    assert api_client.post(ADMIN, {"image": _png()}, format="multipart").status_code in (401, 403)
    assert authenticated_client.post(ADMIN, {"image": _png()}, format="multipart").status_code == 403


def test_a_file_that_is_not_an_image_is_refused(staff_client):
    bad = SimpleUploadedFile("chat.png", b"not an image", content_type="image/png")
    r = staff_client.post(ADMIN, {"image": bad}, format="multipart")
    assert r.status_code == 400
    assert not ChatScreenshot.objects.exists()


def test_staff_can_delete(staff_client, api_client):
    sid = staff_client.post(ADMIN, {"image": _png()}, format="multipart").json()["data"]["id"]
    assert staff_client.delete(ADMIN + f"{sid}/").status_code in (200, 204)
    assert api_client.get(PUBLIC).json()["data"] == []
