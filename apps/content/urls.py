from django.urls import path

from apps.content.views import (
    ContactMessageCreateView,
    FaqListView,
    PageDetailView,
    PageListView,
    SiteBundleView,
)

app_name = "content"

urlpatterns = [
    path("site/", SiteBundleView.as_view(), name="site"),
    path("faqs/", FaqListView.as_view(), name="faqs"),
    path("contact-messages/", ContactMessageCreateView.as_view(), name="contact-create"),
    path("pages/", PageListView.as_view(), name="pages"),
    path("pages/<slug:slug>/", PageDetailView.as_view(), name="page-detail"),
]
