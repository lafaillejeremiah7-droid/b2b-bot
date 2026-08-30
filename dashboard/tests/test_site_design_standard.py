from dashboard.services.site_design_standard import (
    DEFAULT_SITE_DESIGN_STANDARD,
    build_site_generation_prompt,
)


def test_generated_sites_default_to_premium_quality_bar():
    standard = DEFAULT_SITE_DESIGN_STANDARD

    assert standard.minimum_quality_score >= 90
    assert "generic WordPress" in standard.forbidden_traits
    assert any("cinematic" in trait for trait in standard.required_traits)
    assert any("responsive" in trait for trait in standard.required_traits)


def test_prompt_rejects_basic_template_like_output():
    prompt = build_site_generation_prompt(
        business_name="Northstar Roofing",
        business_type="Roofing contractor",
        offer="Free storm-damage inspection",
    )

    assert "top-tier modern AI product studio" in prompt
    assert "custom-designed rather than templated" in prompt
    assert "redesign it" in prompt
    assert "Northstar Roofing" in prompt
    assert "Free storm-damage inspection" in prompt
