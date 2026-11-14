from scripts.ci_test_summary import summarize

REPORT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="1" skipped="2" tests="10" time="3.5">
<testcase classname="tests.test_api" name="test_health" time="0.1"/>
<testcase classname="tests.test_api" name="test_broken" time="0.1">
<failure message="AssertionError: assert 500 == 200">trace</failure></testcase>
</testsuite></testsuites>"""


def test_counts_and_failures(tmp_path):
    path = tmp_path / "report.xml"
    path.write_text(REPORT, encoding="utf-8")
    table = summarize(path)
    assert "| 7 | 1 | 2 | 10 | 3.5 s |" in table
    assert "`tests.test_api::test_broken`: AssertionError: assert 500 == 200" in table


def test_no_failure_list_when_green(tmp_path):
    path = tmp_path / "report.xml"
    path.write_text(REPORT.replace('failures="1"', 'failures="0"').replace(
        '<failure message="AssertionError: assert 500 == 200">trace</failure>', ""), encoding="utf-8")
    assert "Failed tests" not in summarize(path)
