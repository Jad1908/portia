"""`core/feedback`: what a report holds, and what it must never hold.

The rule under test is the allowlist. A report is what the user typed, the
environment and at most one remembered error, and the two paths that say who
and where the user is never reach it.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from portia.core import cancel, feedback


@pytest.fixture(autouse=True)
def _nothing_remembered():
    feedback.forget()
    yield
    feedback.forget()


def _raised(exc: Exception) -> Exception:
    """``exc`` with a traceback on it, as the window's ``except`` would hold it."""
    try:
        raise exc
    except type(exc) as caught:
        return caught


def _through_portia() -> Exception:
    """An error whose traceback passes through portia's own code."""
    from portia.core.io import connect, load_table

    try:
        load_table(Path("/nowhere/AQN_READINGS.csv"), connect()).head()
    except Exception as caught:  # noqa: BLE001 — whatever it raises is the fixture
        return caught
    raise AssertionError("load_table read a file that does not exist")


def test_an_error_is_remembered_with_what_the_user_was_doing():
    problem = feedback.remember(_raised(ValueError("no such column READING_ID")), "run")

    assert problem is not None
    assert (problem.where, problem.kind) == ("run", "ValueError")
    assert problem.headline() == "ValueError: no such column READING_ID"
    assert feedback.latest() is problem


def test_a_stop_is_not_a_failure_and_is_never_remembered():
    assert feedback.remember(_raised(cancel.Cancelled("stopped")), "run") is None
    assert feedback.latest() is None


def test_only_the_last_few_errors_are_kept_newest_first():
    for n in range(feedback.KEPT + 3):
        feedback.remember(_raised(ValueError(str(n))), "run")

    kept = feedback.recent()
    assert len(kept) == feedback.KEPT
    assert kept[0].message == str(feedback.KEPT + 2)


def test_remembering_never_raises_inside_somebody_elses_except():
    class Unprintable(Exception):
        def __str__(self) -> str:
            raise RuntimeError("no")

    assert feedback.remember(Unprintable(), "run") is None


def test_a_frame_inside_portia_is_written_relative_to_the_package():
    problem = feedback.remember(_through_portia(), "indexing")

    inside = [line for line in problem.frames if line.startswith("portia/")]
    assert inside, problem.frames
    assert all(" in " in line and ":" in line for line in inside), inside


def test_the_query_duckdb_quotes_back_is_left_out_of_the_message():
    """The sentence says what went wrong. The ``LINE 1: SELECT …`` under it is
    the user's SQL, and a report holds no query."""
    import duckdb

    try:
        duckdb.sql("SELECT READING_ID FROM AQN_READINGS WHERE STATION = 'secret'")
    except duckdb.Error as caught:
        problem = feedback.remember(caught, "chat")

    assert "AQN_READINGS does not exist" in problem.message
    assert "LINE 1" not in problem.message
    assert "secret" not in problem.message


def test_no_frame_carries_a_path_from_the_users_machine():
    """A path holds a user name and often an employer. This file is outside the
    package, so its own frame is the case: it must come out as a bare word."""
    problem = feedback.remember(_through_portia(), "indexing")

    home = str(Path.home())
    for line in problem.frames:
        assert home not in line, line
        assert line.startswith("portia/") or line.startswith("("), line


def test_the_project_folder_and_the_home_directory_are_taken_out_of_a_message(tmp_path):
    root = tmp_path / "ClientCo" / "pricing"
    root.mkdir(parents=True)
    message = f"cannot read {root}/data/AQN_READINGS.csv, nor {Path.home()}/notes.txt"

    scrubbed = feedback.scrub(message, root=root)

    assert scrubbed == "cannot read <project>/data/AQN_READINGS.csv, nor ~/notes.txt"


def test_the_details_are_the_environment_and_then_the_error_if_asked_for(tmp_path):
    problem = feedback.remember(_raised(ValueError(f"bad file {tmp_path}/x.csv")), "run")
    facts = feedback.environment(provider="anthropic", model="some-model", backend="snowflake")

    without = feedback.details(facts)
    with_it = feedback.details(facts, problem, root=tmp_path)

    assert "- model: anthropic / some-model" in without
    assert "- data: snowflake" in without
    assert "ValueError" not in without
    assert "ValueError: bad file <project>/x.csv" in with_it
    assert "Error during run" in with_it


def test_the_environment_holds_no_path_from_the_users_machine():
    text = feedback.details(feedback.environment())

    assert str(Path.home()) not in text
    assert Path.home().name not in text


def test_the_title_is_the_users_first_line_cut_to_length():
    assert feedback.title("\n  Build fails on a mart  \nmore detail") == "Build fails on a mart"
    long = feedback.title("x" * 200)
    assert len(long) == feedback.TITLE_LENGTH and long.endswith("…")


def test_with_nothing_typed_the_title_is_the_error_and_otherwise_a_stand_in():
    problem = feedback.remember(_raised(KeyError("k")), "chat")

    assert feedback.title("", problem) == "KeyError during chat"
    assert feedback.title("   ") == feedback.UNTITLED


def test_the_issue_link_carries_the_title_and_the_whole_report():
    url, trimmed = feedback.issue_url("It broke", "I pressed Build.", "- portia: 0.0.1")

    query = parse_qs(urlparse(url).query)
    assert url.startswith(feedback.ISSUES_URL + "?")
    assert not trimmed
    assert query["title"] == ["It broke"]
    assert query["body"] == ["I pressed Build.\n\n- portia: 0.0.1"]


def test_a_link_too_long_for_github_says_so_instead_of_dropping_the_details_quietly():
    url, trimmed = feedback.issue_url("It broke", "I pressed Build.", "frame\n" * 3000)

    assert trimmed
    assert len(url) <= feedback.URL_BUDGET
    body = parse_qs(urlparse(url).query)["body"][0]
    assert body == f"I pressed Build.\n\n{feedback.TRIMMED}"


def test_there_is_no_mail_link_without_an_address(monkeypatch):
    monkeypatch.setattr(feedback, "EMAIL", "")

    assert feedback.mail_url("It broke", "said", "written") == ""


def test_a_mail_link_encodes_a_space_as_a_space_and_not_a_plus():
    url = feedback.mail_url("It broke", "two words", "- os: x", address="reports@example.org")

    assert url.startswith("mailto:reports@example.org?subject=")
    assert "+" not in url
    assert unquote(url.split("body=")[1]) == "two words\n\n- os: x"
