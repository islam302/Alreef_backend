"""
Staff content routes, mounted alongside apps.admin_api under
/api/v1/admin-api/. A SimpleRouter (not DefaultRouter) so this module does
not register a second API-root view on the shared prefix.
"""
from django.urls import include, path
from rest_framework.routers import SimpleRouter

from apps.content import views_admin as views

app_name = "content_admin"

router = SimpleRouter()
router.register("contact-phones", views.AdminContactPhoneViewSet, basename="admin-contact-phone")
router.register("branches", views.AdminBranchViewSet, basename="admin-branch")
router.register("social-links", views.AdminSocialLinkViewSet, basename="admin-social-link")
router.register("pages", views.AdminPageViewSet, basename="admin-page")
router.register("page-sections", views.AdminPageSectionViewSet, basename="admin-page-section")
router.register("faqs", views.AdminFaqItemViewSet, basename="admin-faq")
router.register("contact-messages", views.AdminContactMessageViewSet, basename="admin-contact-message")

urlpatterns = [
    path("site-settings/", views.AdminSiteSettingsView.as_view(), name="site-settings"),
    path("", include(router.urls)),
]
