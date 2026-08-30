from dashboard.services.six_employee_pipeline import Lead, SixEmployeePipeline


def test_internal_gmail_self_test_passes_all_six_outbound_employees():
    result = SixEmployeePipeline().run(
        Lead(
            name="Test Owner",
            email="owner@example.com",
            source="internal_gmail_test",
        )
    )

    assert result.approved_to_send is True
    assert [stage["employee"] for stage in result.stages] == [
        "Scout",
        "Researcher",
        "Qualifier",
        "Personalizer",
        "Sales Bot",
        "Manager",
    ]
    assert "internal test" in result.body.lower()
    assert "Employee #7, Closer" in result.body


def test_external_recipient_is_blocked_by_minimal_pipeline():
    result = SixEmployeePipeline().run(
        Lead(name="Prospect", email="prospect@example.com", source="manual")
    )

    assert result.approved_to_send is False
