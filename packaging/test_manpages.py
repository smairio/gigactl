"""Every command the package installs is documented, and the docs match the code.

Two failure modes worth a test. One: a new entry point ships with no man page —
lintian says so, but only if someone reads the build log. Two, worse: a man page
that quietly stops describing the tool, because a subcommand was added or renamed
and the page wasn't touched. The drift tests below read the tools' own usage text
and argv handling, so the documentation cannot silently fall behind.

Run: ``python3 -m pytest packaging -q``
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from conftest import (ROOT, changelog_version, installed_files, long_options,
                      manpages, rendered, shell_subcommands, text)

# tool -> (man page, the section a page for that kind of command belongs in)
DOCUMENTED = {
    "gfan": ("man/gfan.1", "1"),
    "gkbd": ("man/gkbd.1", "1"),
    "gigactl-gui": ("man/gigactl-gui.1", "1"),
    # A system daemon, so section 8 — "commands for the system administrator".
    "gigactld": ("man/gigactld.8", "8"),
}


# --- coverage ---------------------------------------------------------------

def test_every_installed_command_has_a_man_page():
    """The invariant behind lintian's no-manual-page warning, enforced here so a
    new entry point cannot ship undocumented."""
    commands = {Path(path).name for path in installed_files()
                if path.startswith("usr/bin/")}
    assert commands, "expected the package to install some commands"
    assert commands == set(DOCUMENTED), \
        f"undocumented: {sorted(commands - set(DOCUMENTED))}"


@pytest.mark.parametrize("tool", sorted(DOCUMENTED))
def test_the_page_exists_and_is_installed(tool):
    page, _section = DOCUMENTED[tool]
    assert (ROOT / page).exists(), page
    assert page in manpages(), f"{page} is not listed in debian/gigactl.manpages"


def test_nothing_is_listed_that_does_not_exist():
    for page in manpages():
        assert (ROOT / page).exists(), page


# --- roff correctness -------------------------------------------------------

@pytest.mark.parametrize("tool", sorted(DOCUMENTED))
def test_groff_renders_the_page_without_complaint(tool):
    """-z means 'parse, produce no output': all we want are the diagnostics."""
    page, _section = DOCUMENTED[tool]
    proc = subprocess.run(["groff", "-man", "-ww", "-z", str(ROOT / page)],
                          capture_output=True, text=True)
    assert proc.returncode == 0 and not proc.stderr.strip(), \
        f"{page}: {proc.stderr}"


@pytest.mark.parametrize("tool", sorted(DOCUMENTED))
def test_the_title_line_agrees_with_the_filename(tool):
    """A page in man8 whose .TH says 1 lands in the wrong index."""
    page, section = DOCUMENTED[tool]
    body = text(page)
    title = re.search(r'^\.TH\s+"?([\w.-]+)"?\s+"?(\d)"?', body, re.MULTILINE)
    assert title, f"{page} has no usable .TH line"
    assert title.group(1).upper() == tool.upper(), title.group(1)
    assert title.group(2) == section, f"{page}: .TH says section {title.group(2)}"
    assert Path(page).suffix == f".{section}"


@pytest.mark.parametrize("tool", sorted(DOCUMENTED))
def test_the_footer_names_the_version_being_shipped(tool):
    """The .TH line's fourth field is the page footer a reader sees. It is one
    edit per release, and the alternative is a page that quietly claims to
    document a version it does not."""
    page, _section = DOCUMENTED[tool]
    header = text(page).splitlines()[0]
    assert f'"gigactl {changelog_version()}"' in header, header


@pytest.mark.parametrize("tool", sorted(DOCUMENTED))
def test_the_name_section_is_the_shape_apropos_expects(tool):
    """``name \\- one line``. Lintian and mandb both parse this, and a missing
    escape on the dash silently breaks whatis output."""
    page, _section = DOCUMENTED[tool]
    body = text(page)
    name = re.search(r"^\.SH NAME\n(.+)$", body, re.MULTILINE)
    assert name, f"{page} has no NAME section"
    assert re.fullmatch(rf"{re.escape(tool)} \\- \S.*", name.group(1)), name.group(1)


@pytest.mark.parametrize("tool", sorted(DOCUMENTED))
def test_the_page_has_the_sections_a_reader_looks_for(tool):
    page, _section = DOCUMENTED[tool]
    body = text(page)
    for section in ("NAME", "SYNOPSIS", "DESCRIPTION", "SEE ALSO"):
        assert f".SH {section}" in body, f"{page} has no {section}"


def test_cross_references_point_at_pages_we_ship():
    """A SEE ALSO naming one of our own tools must name it in the right section."""
    ours = {tool: section for tool, (_p, section) in DOCUMENTED.items()}
    for tool, (page, _section) in sorted(DOCUMENTED.items()):
        for name, ref_section in re.findall(r"\.BR (\S+) \"?\((\d)\)", text(page)):
            if name in ours:
                assert ref_section == ours[name], \
                    f"{page} cites {name}({ref_section}), not {name}({ours[name]})"


# --- the pages describe the actual tools ------------------------------------

@pytest.mark.parametrize("tool", ["gfan", "gkbd"])
def test_every_subcommand_the_tool_advertises_is_documented(tool):
    """Read from the tool's own usage() text, so adding a subcommand without
    documenting it fails here rather than in a user's terminal."""
    page, _section = DOCUMENTED[tool]
    body = text(page)
    missing = [sub for sub in sorted(shell_subcommands(tool))
               if not re.search(rf"^\.B[RI]? {sub}\b", body, re.MULTILINE)]
    assert not missing, f"{page} documents no {missing}"


@pytest.mark.parametrize("tool,source", [
    ("gigactld", "daemon/gigactld/__main__.py"),
    ("gigactl-gui", "gui/gigactl_gui/__main__.py"),
])
def test_every_option_the_entry_point_accepts_is_documented(tool, source):
    page, _section = DOCUMENTED[tool]
    # rendered(), not text(): an option is written \-\-tray in roff source.
    body = rendered(page)
    missing = [opt for opt in sorted(long_options(source)) if opt not in body]
    assert not missing, f"{page} documents no {missing}"


@pytest.mark.parametrize("tool", ["gfan", "gkbd"])
def test_the_hardware_override_is_documented(tool):
    """The one environment variable a user is genuinely meant to reach for; the
    others exist for the test suite and are deliberately not advertised."""
    page, _section = DOCUMENTED[tool]
    assert f"{tool.upper()}_UNSAFE" in text(page)
    assert ".SH ENVIRONMENT" in text(page)


@pytest.mark.parametrize("tool", ["gfan", "gkbd"])
def test_the_two_modes_are_explained(tool):
    """The single most surprising thing about these tools now: with the daemon up
    they need no root, and without it they re-exec under sudo."""
    body = text(DOCUMENTED[tool][0]).lower()
    assert "daemon" in body and "sudo" in body
