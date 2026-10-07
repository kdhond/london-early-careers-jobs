"""Greenhouse returns its job description HTML-escaped; it must be unescaped into real HTML."""
from jobboard.sources.greenhouse import GreenhouseSource

COMPANY = {"name": "Acme Bio", "token": "acme", "website": "https://acme.bio"}


def test_description_is_unescaped_to_real_html():
    raw = {
        "title": "Scientist", "location": {"name": "London"}, "absolute_url": "https://example.com/1",
        "content": "&lt;div class=&quot;intro&quot;&gt;&lt;p&gt;Gene &amp;amp; cell therapy&lt;/p&gt;&lt;/div&gt;",
    }
    job = GreenhouseSource()._to_job(raw, COMPANY)
    assert job.description_html.startswith('<div class="intro"><p>')   # real tags, not "&lt;div"
    assert "&lt;" not in job.description_html
    assert "Gene & cell therapy" in job.description_text               # plain text stays clean
