from careeros.core.ids import make_company_id, make_compensation_id, make_person_id


def test_make_company_id_is_deterministic():
    assert make_company_id("Acme Corp") == make_company_id("Acme Corp")


def test_make_company_id_slugifies():
    company_id = make_company_id("Acme Corp!")
    assert company_id == "acme-corp"


def test_make_person_id_is_deterministic():
    company_id = make_company_id("Acme Corp")
    assert make_person_id("Jane Doe", company_id) == make_person_id("Jane Doe", company_id)


def test_make_person_id_differs_across_companies():
    id_a = make_person_id("Jane Doe", make_company_id("Acme Corp"))
    id_b = make_person_id("Jane Doe", make_company_id("Beta Inc"))
    assert id_a != id_b


def test_make_compensation_id_is_non_deterministic():
    id_a = make_compensation_id("Acme Corp", "Senior SRE")
    id_b = make_compensation_id("Acme Corp", "Senior SRE")
    assert id_a != id_b


def test_make_compensation_id_includes_slugified_company_and_role():
    comp_id = make_compensation_id("Acme Corp", "Senior SRE")
    assert "acme-corp" in comp_id
    assert "senior-sre" in comp_id
