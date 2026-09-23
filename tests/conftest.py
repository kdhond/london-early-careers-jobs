"""Shared pytest fixtures for the jobboard test suite."""
import pytest

from jobboard.models import ApplyLink, Job
from jobboard.sources.base import load_yaml


@pytest.fixture(scope="session")
def settings():
    """The real config/settings.yaml — tests run against actual project config, not a fake copy."""
    return load_yaml("settings.yaml")


@pytest.fixture
def make_job():
    """Factory fixture: make_job(title=..., company=..., ...) -> Job, for building test data tersely."""

    def _make(
        title="Graduate Analyst",
        company="Acme",
        location="London, England, United Kingdom",
        source="test",
        description_text="",
        description_html=None,
        description_is_full=False,
        seniority=None,
        city=None,
        **kwargs,
    ):
        job = Job.new(
            title=title,
            company=company,
            location=location,
            source=source,
            description_text=description_text,
            description_html=description_html if description_html is not None else description_text,
            description_is_full=description_is_full,
            seniority=seniority,
            apply_links=[ApplyLink(source=source, url=f"https://{source}.example/{title}")],
            **kwargs,
        )
        if city is not None:
            job.city = city
        return job

    return _make
