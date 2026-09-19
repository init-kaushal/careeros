from careeros.core.ids import make_company_id, make_person_id


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
