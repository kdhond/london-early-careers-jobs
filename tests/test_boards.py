"""Parsing tests for the jobs.ac.uk and CharityJob scrapers (synthetic markup)."""
from jobboard.sources.boards import CharityJobSource, JobsAcUkSource

JA = """<div class="j-search-result__result x"><div class="j-search-result__text">
<a href="/job/AAA111/research-associate-in-biology"> Research Associate in Biology </a>
<div class="j-search-result__employer"><b>University of Bristol</b></div>
<div>Location:
 Bristol
</div><div class="j-search-result__info"><strong>Salary: </strong> £39,000 </div>
<strong>Date Placed: </strong>29 Sep
</div><div class="j-search-result__date-logos"></div></div>
<div class="j-search-result__result x"><a href="/job/BBB222/professor-of-x">Professor of X</a>
<div class="j-search-result__employer"><b>U</b></div></div>"""

CJ = """<div class="job-card" job-id="1"><div class="job-summary"><div class="job-title">
<a href="https://www.charityjob.co.uk/jobs/org/research-officer/1">Research Officer</a></div>
<div class="organisation">Health Org, London (Hybrid)</div></div></div>"""


def test_jobsacuk_parses_and_drops_senior():
    src = JobsAcUkSource.__new__(JobsAcUkSource)
    jobs = src._parse(JA)
    assert [j.title for j in jobs] == ["Research Associate in Biology"]
    assert jobs[0].company == "University of Bristol" and jobs[0].location == "Bristol"


def test_charityjob_splits_employer_and_location():
    src = CharityJobSource.__new__(CharityJobSource)
    job = src._parse(CJ)[0]
    assert (job.title, job.company, job.location) == ("Research Officer", "Health Org", "London (Hybrid)")
