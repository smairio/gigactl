"""The release workflow, checked without pushing a tag.

A release workflow only ever runs when it matters, and its failures are the
expensive kind: a tag is already public by the time you find out. Everything here
is something that would only surface on a real `v*` push — an unpinned runner, a
missing `contents: write`, a shallow checkout that breaks `git describe`, an
artifact path that does not match what dpkg-buildpackage actually wrote.

Run: ``python3 -m pytest packaging -q``
"""
from __future__ import annotations

import re
import subprocess

import pytest
from conftest import (ROOT, RELEASE_WORKFLOW, apt_installed, build_depends,
                      changelog_version, commands, control_field, only_job,
                      run_steps, text, workflow, workflow_commands,
                      workflow_shell)


# --- when it runs -----------------------------------------------------------

def test_it_triggers_on_version_tags_and_nothing_else():
    """A branch push must not publish a release."""
    triggers = workflow()["on"]
    assert set(triggers) == {"push"}, sorted(triggers)
    assert triggers["push"] == {"tags": ["v*"]}


def test_the_runner_is_pinned_to_noble():
    """The .deb is built against noble's debhelper and python3 and targets noble;
    ubuntu-latest moves when GitHub promotes the next LTS."""
    assert only_job()["runs-on"] == "ubuntu-24.04"


def test_the_workflow_may_write_releases():
    """Without this the upload fails at the last step, after a public tag."""
    assert workflow()["permissions"]["contents"] == "write"


# --- the tag/changelog agreement --------------------------------------------

def test_the_tag_must_match_the_changelog_version():
    """debian/changelog is the version source. A v1.0.1 tag on a 1.0.0 changelog
    would otherwise publish gigactl_1.0.0_amd64.deb under the wrong release."""
    shell = workflow_shell()
    assert "dpkg-parsechangelog" in shell
    assert "exit 1" in shell, "the mismatch has to fail the job"


def test_the_tag_name_comes_from_the_ref_not_from_git_describe():
    """actions/checkout is shallow by default and fetches no tags, so
    `git describe` would fail or lie. github.ref_name is exact."""
    assert "github.ref_name" in text(RELEASE_WORKFLOW)
    assert "git describe" not in workflow_commands()  # a comment may mention it


def test_untrusted_ref_values_reach_the_shell_through_the_environment():
    """A tag name is attacker-controlled input; interpolating ${{ }} straight
    into a run: script is the documented script-injection hole."""
    for name, script in run_steps():
        assert "${{" not in script, f"{name} interpolates into shell directly"


# --- what it builds with ----------------------------------------------------

def test_build_dependencies_come_from_debian_control():
    """One list, not two. A hand-copied apt-get install line is how a new
    Build-Depends gets forgotten until the release build fails."""
    assert "apt-get build-dep" in workflow_shell()


@pytest.mark.parametrize("package", build_depends())
def test_no_build_dependency_is_installed_by_name(package):
    """Exact names, not substrings: python3 is a Build-Depends and python3-pytest
    is release tooling, and only one of those belongs in the install line."""
    assert package not in apt_installed(), \
        f"{package} is a Build-Depends; let apt-get build-dep pull it"


def _pytest_invocations() -> list[list[str]]:
    """Every `python3 -m pytest …` command line in the workflow, tokenised."""
    return [line.split() for line in workflow_commands().splitlines()
            if "-m pytest" in line]


def test_the_suites_that_need_no_gui_stack_run_before_the_build():
    """packaging/ reads debian/ the way dpkg-buildpackage will and cli/ drives the
    shell tools against stubs — both are pure Python, no GTK typelibs needed. A
    release should not be able to ship a .deb whose own contract tests fail."""
    scripts = [commands(script) for _n, script in run_steps()]
    # "-m pytest", not "pytest" — the apt step installs python3-pytest.
    test_step = next((i for i, s in enumerate(scripts) if "-m pytest" in s), None)
    build_step = next((i for i, s in enumerate(scripts) if "dpkg-buildpackage" in s),
                      None)
    assert test_step is not None, "the workflow runs no tests"
    assert build_step is not None, "the workflow never builds the package"
    assert test_step < build_step, "test before building, not after"
    suites = {arg for invocation in _pytest_invocations() for arg in invocation}
    assert {"packaging", "cli"} <= suites, sorted(suites)


def test_each_suite_gets_its_own_pytest_invocation():
    """`pytest packaging cli` cannot work: both suites keep their readers in a
    top-level conftest.py, so one is imported as the module `conftest` and the
    other is not, and every `from conftest import ...` in the loser fails to
    collect. Found by running the workflow's own steps locally — it would
    otherwise have failed on the first real tag."""
    for invocation in _pytest_invocations():
        suites = [arg for arg in invocation if arg in ("packaging", "cli")]
        assert len(suites) == 1, f"one suite per invocation, got {suites}"


# --- what it publishes ------------------------------------------------------

def test_the_artifact_path_matches_what_dpkg_buildpackage_writes():
    """dpkg-buildpackage puts the .deb in the *parent* directory, named
    <source>_<version>_<arch>.deb. Getting any part of that wrong means the upload
    step finds nothing."""
    shell = workflow_shell()
    expected = f"../gigactl_${{VERSION}}_{control_field('Architecture')}.deb"
    assert expected in shell, f"expected the workflow to reference {expected}"
    # and the version really is the one the guard exported
    assert "VERSION=" in shell and "GITHUB_ENV" in shell


def test_the_release_is_published_with_the_preinstalled_gh_cli():
    """Fewer third-party actions in a workflow that holds contents: write."""
    shell = workflow_shell()
    assert "gh release create" in shell
    uses = [step["uses"] for step in only_job()["steps"] if "uses" in step]
    assert all(u.startswith("actions/checkout@") for u in uses), uses


def test_gh_gets_a_token():
    assert "GH_TOKEN" in text(RELEASE_WORKFLOW)


def test_a_re_run_updates_the_release_instead_of_failing():
    """A tag cannot be pushed twice, so re-running the job after a transient
    failure must be able to finish rather than dying on 'already exists'."""
    shell = workflow_commands()
    assert "gh release view" in shell
    assert "gh release upload" in shell and "--clobber" in shell


def test_the_release_notes_come_from_the_changelog_entry():
    """The changelog already says what changed; writing it twice invites drift."""
    assert "--notes-file" in workflow_shell()


# --- the shell itself -------------------------------------------------------

@pytest.mark.parametrize("index", range(len(run_steps())))
def test_every_run_step_is_valid_shell(index):
    name, script = run_steps()[index]
    proc = subprocess.run(["bash", "-n"], input=script, capture_output=True,
                          text=True)
    assert proc.returncode == 0, f"{name}: {proc.stderr}"


def test_the_workflow_is_reachable_from_the_documented_version():
    """A sanity join: the tag a maintainer would push for today's changelog."""
    assert re.fullmatch(r"\d+\.\d+\.\d+", changelog_version())
    assert (ROOT / RELEASE_WORKFLOW).exists()
