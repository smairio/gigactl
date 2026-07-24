"""Readers for the packaging suite: ``debian/`` as dpkg-buildpackage will see it.

The tests next door are assertions only; the parsing lives here, the way
``daemon/conftest.py`` holds that suite's shared machinery. Nothing here builds
or installs anything — every reader works off the files in the tree, so the suite
runs on a machine with no debhelper and no root.

Run: ``python3 -m pytest packaging -q`` (or ``cd packaging && python3 -m pytest``).
"""
from __future__ import annotations

import ast
import configparser
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEBIAN = ROOT / "debian"

# debian/rules generates these install sources at build time, so they are absent
# from a clean checkout and cannot be checked for existence.
BUILT_BY_RULES = "debian/icons/"

PACKAGE_DIRS = {"gigactld": "daemon", "gigactl_gui": "gui"}


def text(rel: str) -> str:
    return (ROOT / rel).read_text()


def rules_variable(name: str) -> str:
    """One ``NAME = value`` declaration from debian/rules."""
    match = re.search(rf"^{name}\s*=\s*(.*)$", text("debian/rules"), re.MULTILINE)
    assert match, f"debian/rules should declare {name}"
    return match.group(1).strip()


def private_dir() -> str:
    """The private module directory, without its leading slash (as dpkg paths go).

    debian/rules is the single declaration; everything else is checked against it.
    """
    return rules_variable("PRIVATE_DIR").lstrip("/")


def install_lines() -> list[tuple[str, str]]:
    """``debian/install`` as (source glob, destination dir) pairs."""
    pairs = []
    for line in text("debian/install").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        assert len(fields) == 2, f"expected 'source dest', got {line!r}"
        pairs.append((fields[0], fields[1]))
    return pairs


def link_lines() -> list[tuple[str, str]]:
    """``debian/links`` as (target, symlink) pairs, both in-package paths."""
    pairs = []
    for line in text("debian/links").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        assert len(fields) == 2, f"expected 'target link', got {line!r}"
        pairs.append((fields[0], fields[1]))
    return pairs


def installed_files() -> dict[str, str]:
    """Every path the package provides -> where it came from.

    Globs are expanded against the tree; sources debian/rules generates keep
    their literal name, since they do not exist until it has run. Symlinks from
    debian/links count as provided paths — /usr/bin/gigactl-gui is one, and a
    caller asking "does the package provide this binary" means either.
    """
    files = {}
    for source, dest in install_lines():
        if source.startswith(BUILT_BY_RULES):
            files[f"{dest}/{Path(source).name}"] = source
            continue
        for match in sorted(ROOT.glob(source)):
            files[f"{dest}/{match.name}"] = str(match.relative_to(ROOT))
    for target, link in link_lines():
        files[link] = f"symlink -> {target}"
    return files


def desktop_entry(rel: str) -> configparser.SectionProxy:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(ROOT / rel)
    return parser["Desktop Entry"]


def unit_values(rel: str, key: str) -> list[str]:
    """A systemd unit's values for one key (units allow repeats, so a list)."""
    return [m.group(1).strip() for m in
            re.finditer(rf"^{key}=(.*)$", text(rel), re.MULTILINE)]


def control_field(field: str) -> str:
    """One field of the *binary* package stanza, unwrapped onto a single line."""
    stanza = text("debian/control").split("\nPackage: ")[1]
    match = re.search(rf"^{field}:(.*?)(?=\n\S|\Z)", stanza, re.MULTILINE | re.DOTALL)
    assert match, f"debian/control's binary stanza has no {field}"
    return " ".join(match.group(1).split())


def icon_sizes() -> list[str]:
    return rules_variable("ICON_SIZES").split()


def changelog_version() -> str:
    return subprocess.run(
        ["dpkg-parsechangelog", "-l", str(DEBIAN / "changelog"), "-S", "Version"],
        capture_output=True, text=True, check=True).stdout.strip()


def launcher_programs() -> dict[str, str]:
    """The launcher's ``PROGRAMS`` map: invoked name -> package it runs."""
    match = re.search(r"^PROGRAMS = (\{.*?\})$", text("debian/bin/launcher"),
                      re.MULTILINE | re.DOTALL)
    assert match, "debian/bin/launcher should declare a PROGRAMS map"
    return ast.literal_eval(match.group(1))


def pyproject_scripts(rel: str) -> dict[str, str]:
    """A pyproject's ``[project.scripts]`` as name -> "module:function"."""
    section = text(rel).split("[project.scripts]")[1].split("\n[")[0]
    return dict(re.findall(r'^(\S+) = "(.+?)"$', section, re.MULTILINE))
