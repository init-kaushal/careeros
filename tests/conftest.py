import pytest
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace, WorkspaceContext


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(pytest.mark.skip(reason="requires live browser — run with pytest -m integration"))

SAMPLE_RESUME = """
Alice Johnson
Senior Site Reliability Engineer | San Francisco, CA
alice@example.com

EXPERIENCE
Site Reliability Engineer — MegaCorp (2018–present, 6 years)
  - Built distributed monitoring platform handling 1M events/sec
  - Reduced MTTR by 40% through improved alerting and runbooks

Software Engineer — StartupXYZ (2016–2018)
  - Built microservices in Go and Python

SKILLS
Python, Go, Kubernetes, Terraform, AWS, Prometheus, Grafana, Linux

EDUCATION
BS Computer Science, UC Berkeley, 2016
"""


@pytest.fixture
def tmp_workspace(tmp_path) -> WorkspaceContext:
    storage = LocalFilesystemStorage(str(tmp_path))
    return init_workspace(storage)
