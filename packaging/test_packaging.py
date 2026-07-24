"""What the .deb promises the rest of the tree.

Packaging is declarative, so the failures it invites are silent ones: a module
that never makes it into the package, a ``.desktop`` naming a binary nobody
installs (which is exactly how systemd's autostart generator came to skip our
tray entry), a unit whose ``ExecStart`` points at a path the package doesn't
own. None of that shows up until someone installs the thing on a clean machine.

These tests read ``debian/`` the way ``dpkg-buildpackage`` will and assert those
joins hold. No build required: anything ``debian/rules`` generates is checked
against the rule that generates it, not against a file on disk.

Run: ``python3 -m pytest packaging -q``
"""
from __future__ import annotations

import configparser
import re
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEBIAN = ROOT / "debian"
PRIVATE_DIR = "usr/lib/gigactl"          # Debian Python Policy: private modules
GENERATED = "debian/icons/"              # produced by debian/rules, not in git


# --- readers ----------------------------------------------------------------

def _text(rel: str) -> str:
    return (ROOT / rel).read_text()


def install_lines() -> list[tuple[str, str]]:
    """``debian/install`` as (source glob, destination dir) pairs."""
    pairs = []
    for line in _text("debian/install").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        assert len(fields) == 2, f"expected 'source dest', got {line!r}"
        pairs.append((fields[0], fields[1]))
    return pairs


def installed_files() -> dict[str, str]:
    """Every file the package installs, as absolute-in-package path -> source.

    Globs are expanded against the tree; generated sources keep their literal
    name, since they do not exist until ``debian/rules`` has run.
    """
    files = {}
    for source, dest in install_lines():
        if source.startswith(GENERATED):
            files[f"{dest}/{Path(source).name}"] = source
            continue
        matches = sorted(ROOT.glob(source))
        for match in matches:
            files[f"{dest}/{match.name}"] = str(match.relative_to(ROOT))
    return files


def desktop_entry(rel: str) -> configparser.SectionProxy:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(ROOT / rel)
    return parser["Desktop Entry"]


def unit_values(rel: str, key: str) -> list[str]:
    """A systemd unit's values for one key (units allow repeats, so a list)."""
    return [m.group(1).strip() for m in
            re.finditer(rf"^{key}=(.*)$", _text(rel), re.MULTILINE)]


def control_field(field: str) -> str:
    """One field of the *binary* package stanza, unwrapped onto a single line."""
    stanza = _text("debian/control").split("\nPackage: ")[1]
    match = re.search(rf"^{field}:(.*?)(?=\n\S|\Z)", stanza, re.MULTILINE | re.DOTALL)
    assert match, f"debian/control's binary stanza has no {field}"
    return " ".join(match.group(1).split())


def icon_sizes() -> list[str]:
    match = re.search(r"^ICON_SIZES\s*=\s*(.*)$", _text("debian/rules"), re.MULTILINE)
    assert match, "debian/rules should define ICON_SIZES"
    return match.group(1).split()


# --- the sources exist ------------------------------------------------------

def test_every_installed_source_exists():
    for source, _dest in install_lines():
        if source.startswith(GENERATED):
            continue
        assert sorted(ROOT.glob(source)), f"debian/install: {source} matches nothing"


def test_nothing_is_installed_into_usr_local():
    """/usr/local belongs to the admin — Debian Policy 9.1.2."""
    for _source, dest in install_lines():
        assert not dest.startswith("usr/local"), dest


@pytest.mark.parametrize("package", ["gigactld", "gigactl_gui"])
def test_every_python_module_is_packaged(package):
    """A new module must not be able to slip out of the .deb unnoticed."""
    source_dir = {"gigactld": "daemon", "gigactl_gui": "gui"}[package]
    modules = {p.name for p in (ROOT / source_dir / package).glob("*.py")}
    packaged = {Path(p).name for p in installed_files()
                if p.startswith(f"{PRIVATE_DIR}/{package}/")}
    assert modules, f"expected {package} to have modules"
    assert modules <= packaged, f"missing from the .deb: {sorted(modules - packaged)}"


def test_tests_and_fixtures_are_not_packaged():
    for path in installed_files():
        assert "conftest" not in path and "/tests/" not in path, path


# --- the joins that break silently ------------------------------------------

@pytest.mark.parametrize("entry", ["gui/data/io.github.smairio.gigactl.desktop",
                                   "gui/data/gigactl-tray.desktop"])
def test_desktop_exec_names_a_binary_the_package_installs(entry):
    """systemd's XDG autostart generator skips any entry whose Exec= is not on
    PATH, so a .desktop naming a binary we don't install is silently dead."""
    binary = desktop_entry(entry)["Exec"].split()[0]
    assert f"usr/bin/{binary}" in installed_files(), binary


@pytest.mark.parametrize("entry", ["gui/data/io.github.smairio.gigactl.desktop",
                                   "gui/data/gigactl-tray.desktop"])
def test_desktop_icon_names_an_icon_the_package_installs(entry):
    name = desktop_entry(entry)["Icon"]
    installed = installed_files()
    assert any(path.startswith("usr/share/icons/hicolor/")
               and Path(path).stem == name for path in installed), name


@pytest.mark.parametrize("key", ["ExecStart", "ExecStopPost"])
def test_unit_commands_are_binaries_the_package_installs(key):
    installed = installed_files()
    values = unit_values("daemon/data/gigactl.service", key)
    assert values, f"the unit has no {key}"
    for value in values:
        binary = value.lstrip("-+!@").split()[0]
        assert binary.startswith("/"), f"{key} must be an absolute path: {value}"
        assert binary.lstrip("/") in installed, binary


def test_the_unit_ships_where_packages_ship_units():
    """Debian Policy 9.3.2: /usr/lib/systemd/system, never /etc."""
    assert "usr/lib/systemd/system/gigactl.service" in installed_files()


@pytest.mark.parametrize("source,dest", [
    ("daemon/data/io.github.smairio.gigactl.conf", "usr/share/dbus-1/system.d"),
    ("daemon/data/io.github.smairio.gigactl.policy", "usr/share/polkit-1/actions"),
])
def test_system_policy_goes_to_the_package_directory_not_etc(source, dest):
    assert (source, dest) in install_lines()


# --- the launchers ----------------------------------------------------------

@pytest.mark.parametrize("launcher,pyproject,module", [
    ("debian/bin/gigactld", "daemon/pyproject.toml", "gigactld"),
    ("debian/bin/gigactl-gui", "gui/pyproject.toml", "gigactl_gui"),
])
def test_launcher_calls_the_same_entry_point_as_the_pyproject(launcher, pyproject,
                                                              module):
    """The .deb and a pip install must start the app the same way."""
    declared = re.search(rf'^{Path(launcher).name} = "(.+?)"$', _text(pyproject),
                         re.MULTILINE)
    assert declared, f"{pyproject} declares no {Path(launcher).name} script"
    target, function = declared.group(1).split(":")
    body = _text(launcher)
    assert f"from {target} import {function}" in body, declared.group(1)
    assert module in target


@pytest.mark.parametrize("launcher", ["debian/bin/gigactld", "debian/bin/gigactl-gui"])
def test_launcher_puts_the_private_dir_on_the_path(launcher):
    assert f'"/{PRIVATE_DIR}"' in _text(launcher)


@pytest.mark.parametrize("script", ["debian/bin/gigactld", "debian/bin/gigactl-gui",
                                    "debian/rules", "debian/preinst",
                                    "debian/postinst", "debian/postrm"])
def test_shipped_scripts_are_executable(script):
    assert (ROOT / script).stat().st_mode & stat.S_IXUSR, script


# --- icons ------------------------------------------------------------------

def test_every_generated_icon_is_both_built_and_installed():
    rules, installed = _text("debian/rules"), installed_files()
    assert "rsvg-convert" in rules
    for size in icon_sizes():
        assert f"usr/share/icons/hicolor/{size}x{size}/apps/" \
               "io.github.smairio.gigactl.png" in installed, size
    assert "usr/share/icons/hicolor/scalable/apps/" \
           "io.github.smairio.gigactl.svg" in installed
    assert "usr/share/icons/hicolor/symbolic/apps/" \
           "io.github.smairio.gigactl-symbolic.svg" in installed


def test_generated_files_are_cleaned():
    """Otherwise a second dpkg-buildpackage packages the first one's leftovers —
    and it has to be an override, because dh_clean's own list is only `rm -f` and
    fails outright on a directory."""
    rules = _text("debian/rules")
    assert re.search(r"^ICON_DIR\s*=\s*" + re.escape(GENERATED.rstrip("/")) + r"\s*$",
                     rules, re.MULTILINE)
    assert re.search(r"^override_dh_clean:\n\trm -rf \$\(ICON_DIR\)$", rules,
                     re.MULTILINE)


def test_the_symbolic_icon_is_monochrome():
    """A symbolic icon the theme can recolour: one flat fill, no gradients."""
    svg = _text("design/icon-symbolic.svg")
    assert "Gradient" not in svg
    assert len(set(re.findall(r'fill="(#[0-9a-fA-F]{3,6})"', svg))) <= 1


# --- control / changelog ----------------------------------------------------

@pytest.mark.parametrize("dependency", ["python3-gi", "gir1.2-gtk-4.0",
                                        "gir1.2-adw-1", "${python3:Depends}",
                                        "${misc:Depends}"])
def test_runtime_dependencies_are_declared(dependency):
    assert dependency in control_field("Depends")


def test_nothing_depends_on_a_package_apt_cannot_resolve():
    """nbfc-linux (ec_probe) is GitHub-only: a dep on it makes the .deb
    uninstallable from a plain apt run."""
    assert "nbfc" not in _text("debian/control")


def test_build_depends_cover_what_rules_actually_runs():
    build = _text("debian/control")
    for tool, package in [("rsvg-convert", "librsvg2-bin"),
                          ("dh_python3", "dh-python")]:
        if tool in _text("debian/rules"):
            assert package in build, f"{tool} needs Build-Depends: {package}"


def test_dh_python3_is_told_about_the_private_directory():
    """Without the explicit dir it byte-compiles nothing: the modules are not in
    dist-packages."""
    assert f"dh_python3 /{PRIVATE_DIR}" in _text("debian/rules")


def test_the_changelog_parses_and_names_this_package():
    out = subprocess.run(["dpkg-parsechangelog", "-l", str(DEBIAN / "changelog")],
                         capture_output=True, text=True, check=True).stdout
    assert re.search(r"^Source: gigactl$", out, re.MULTILINE), out
    version = re.search(r"^Version: (.+)$", out, re.MULTILINE)
    assert version and re.fullmatch(r"\d+\.\d+\.\d+", version.group(1)), out


@pytest.mark.parametrize("source", ["daemon/gigactld/__init__.py",
                                    "gui/gigactl_gui/__init__.py",
                                    "daemon/pyproject.toml", "gui/pyproject.toml"])
def test_one_version_everywhere(source):
    """The daemon publishes its ``__version__`` as the DaemonVersion property, so
    a changelog that disagrees ships a package whose own API misreports it."""
    packaged = subprocess.run(
        ["dpkg-parsechangelog", "-l", str(DEBIAN / "changelog"), "-S", "Version"],
        capture_output=True, text=True, check=True).stdout.strip()
    assert f'"{packaged}"' in _text(source), f"{source} disagrees with {packaged}"


def test_a_native_package_has_no_upstream_tarball():
    assert "3.0 (native)" in _text("debian/source/format")


# --- maintainer scripts -----------------------------------------------------

@pytest.mark.parametrize("script", ["preinst", "postinst", "postrm"])
def test_maintainer_scripts_are_strict_and_leave_room_for_debhelper(script):
    body = _text(f"debian/{script}")
    assert "set -e" in body
    assert "#DEBHELPER#" in body, "dh's own snippets have nowhere to go"


def test_preinst_guards_the_hardware_like_install_sh():
    body = _text("debian/preinst")
    assert "sys_vendor" in body and "product_name" in body
    assert "GIGACTL_FORCE_INSTALL" in body, "there must be an escape hatch"


def test_postinst_supersedes_both_legacy_keyboard_restore_paths():
    """Issue #22: gkbd-restore.service and the sleep hook re-apply the backlight
    from /var/lib/gkbd, racing the daemon's own /var/lib/gigactl state."""
    body = _text("debian/postinst")
    assert "gkbd-restore.service" in body
    assert "/lib/systemd/system-sleep/gkbd" in body
    assert "disable" in body


def test_purge_removes_the_daemon_state():
    body = _text("debian/postrm")
    assert "purge" in body and "/var/lib/gigactl" in body


def test_the_state_directory_is_owned_by_the_package():
    assert "var/lib/gigactl" in _text("debian/dirs")
