import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from dashboard.models import OutreachSuppression, SuppressionReason


@pytest.mark.django_db
def test_dashboard_requires_authentication(client):
    response = client.get(reverse("company_dashboard"))

    assert response.status_code == 302
    assert response.url == "/admin/login/?next=/dashboard/"


@pytest.mark.django_db
@override_settings(
    GOOGLE_MAPS_API_KEY="maps-test-key",
    SERPAPI_API_KEY="search-test-key",
    OUTREACH_EMAIL="sender@example.com",
    OUTREACH_PHONE="555-0100",
)
def test_authenticated_dashboard_renders_eight_employee_formation_and_real_suppression_count(client):
    user = get_user_model().objects.create_user(
        username="owner",
        password="test-password",
    )
    OutreachSuppression.objects.create(
        normalized_email="blocked@example.com",
        reason=SuppressionReason.MANUAL,
    )
    client.force_login(user)

    response = client.get(reverse("company_dashboard"))
    html = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "Eight-player operating squad" in html
    for employee in (
        "Scout",
        "Researcher",
        "Qualifier",
        "Personalizer",
        "Sales Bot",
        "Manager",
        "Closer",
        "Boss",
    ):
        assert employee in html
    assert "1" in html
    assert "durable do-not-contact records" in html
    assert "3/4" in html
    assert "Awaiting persisted pipeline telemetry" in html
    assert html.count("configured") >= 3


@pytest.mark.django_db
def test_dashboard_does_not_fake_unpersisted_pipeline_kpis(client):
    user = get_user_model().objects.create_user(
        username="operator",
        password="test-password",
    )
    client.force_login(user)

    response = client.get(reverse("company_dashboard"))
    html = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "persisted pipeline samples" in html
    assert "Awaiting persisted pipeline telemetry" in html
    assert "100% close rate" not in html
    assert "revenue" not in html.lower()


@pytest.mark.django_db
def test_root_redirects_to_dashboard(client):
    response = client.get(reverse("home"))

    assert response.status_code == 302
    assert response.url == "/dashboard/"
