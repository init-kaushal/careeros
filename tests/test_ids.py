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


def test_make_approval_id_slugifies_and_suffixes():
    from careeros.core.ids import make_approval_id
    approval_id = make_approval_id("send_outreach", "acme-sre-abc1__acme-corp-jane-doe")
    assert approval_id.startswith("send-outreach-acme-sre-abc1")
    assert len(approval_id.split("-")[-1]) == 6


def test_make_approval_id_is_unique_per_call():
    from careeros.core.ids import make_approval_id
    first = make_approval_id("send_outreach", "job-1")
    second = make_approval_id("send_outreach", "job-1")
    assert first != second


def test_make_approval_id_handles_none_entity():
    from careeros.core.ids import make_approval_id
    approval_id = make_approval_id("send_outreach", None)
    assert approval_id.startswith("send-outreach-")
    assert "--" not in approval_id


def test_make_approval_id_strips_path_traversal():
    from careeros.core.ids import make_approval_id
    approval_id = make_approval_id("send_outreach", "../../etc/passwd")
    assert "/" not in approval_id
    assert ".." not in approval_id
