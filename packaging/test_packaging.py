"""What the .deb promises the rest of the tree.

Packaging is declarative, so the failures it invites are silent ones: a module
that never makes it into the package, a ``.desktop`` naming a binary nobody
installs (which is exactly how systemd's autostart generator came to skip our
tray entry), a unit whose ``ExecStart`` points at a path the package doesn't
own. None of that shows up until someone installs the thing on a clean machine.

These tests read ``debian/`` the way ``dpkg-buildpackage`` will and assert those
joins hold. No build required: anything ``debian/rules`` generates is checked
against the rule that generates it, not against a file on disk. The parsing lives
in ``conftest.py``.

Run: ``python3 -m pytest packaging -q``
"""
from __future__ import annotations

import re
import stat
from pathlib import Path

import pytest
from conftest import (BUILT_BY_RULES, PACKAGE_DIRS, ROOT, changelog_version,
                      control_field, desktop_entry, icon_sizes, install_lines,
                      installed_files, launcher_programs, private_dir,
                      pyproject_scripts, rules_variable, text, unit_values)

DESKTOP_ENTRIES = ["gui/data/io.github.smairio.gigactl.desktop",
                   "gui/data/gigactl-tray.desktop"]
UNIT = "daemon/data/gigactl.service"


# --- the sources exist ------------------------------------------------------

def test_every_installed_source_exists():
    for source, _dest in install_lines():
        if source.startswith(BUILT_BY_RULES):
            continue
        assert sorted(ROOT.glob(source)), f"debian/install: {source} matches nothing"


def test_nothing_is_installed_into_usr_local():
    """/usr/local belongs to the admin — Debian Policy 9.1.2."""
    for _source, dest in install_lines():
        assert not dest.startswith("usr/local"), dest


@pytest.mark.parametrize("package", sorted(PACKAGE_DIRS))
def test_every_python_module_is_packaged(package):
    """A new module must not be able to slip out of the .deb unnoticed."""
    modules = {p.name for p in (ROOT / PACKAGE_DIRS[package] / package).glob("*.py")}
    packaged = {Path(p).name for p in installed_files()
                if p.startswith(f"{private_dir()}/{package}/")}
    assert modules, f"expected {package} to have modules"
    assert modules <= packaged, f"missing from the .deb: {sorted(modules - packaged)}"


@pytest.mark.parametrize("package", sorted(PACKAGE_DIRS))
def test_a_package_directory_holds_only_python(package):
    """debian/install globs ``*.py``, so a .ui, .css or .gresource added beside
    the modules would be dropped silently. Adding one has to fail here first, and
    be given its own install line."""
    source_dir = ROOT / PACKAGE_DIRS[package] / package
    strays = [p.name for p in source_dir.iterdir()
              if p.is_file() and p.suffix != ".py"]
    assert not strays, f"not covered by debian/install's *.py glob: {strays}"


def test_tests_and_fixtures_are_not_packaged():
    for path in installed_files():
        assert "conftest" not in path and "/tests/" not in path, path


# --- the joins that break silently ------------------------------------------

@pytest.mark.parametrize("entry", DESKTOP_ENTRIES)
def test_desktop_exec_names_a_binary_the_package_installs(entry):
    """systemd's XDG autostart generator skips any entry whose Exec= is not on
    PATH, so a .desktop naming a binary we don't install is silently dead."""
    binary = desktop_entry(entry)["Exec"].split()[0]
    assert f"usr/bin/{binary}" in installed_files(), binary


@pytest.mark.parametrize("entry", DESKTOP_ENTRIES)
def test_desktop_icon_names_an_icon_the_package_installs(entry):
    name = desktop_entry(entry)["Icon"]
    installed = installed_files()
    assert any(path.startswith("usr/share/icons/hicolor/")
               and Path(path).stem == name for path in installed), name


@pytest.mark.parametrize("key", ["ExecStart", "ExecStopPost"])
def test_unit_commands_are_binaries_the_package_installs(key):
    installed = installed_files()
    values = unit_values(UNIT, key)
    assert values, f"the unit has no {key}"
    for value in values:
        binary = value.lstrip("-+!@").split()[0]
        assert binary.startswith("/"), f"{key} must be an absolute path: {value}"
        assert binary.lstrip("/") in installed, binary


def test_the_unit_loads_ec_sys_before_starting():
    """The daemon chooses its EC backend once, at startup, and modules-load.d only
    fires at boot — so without this the first run after an install comes up on the
    /dev/port fallback that races the kernel's own EC driver, and stays there."""
    values = unit_values(UNIT, "ExecStartPre")
    assert any(re.match(r"-?/s?bin/modprobe ec_sys", v) for v in values), values
    assert all(v.startswith("-") for v in values), \
        "a kernel without ec_sys must not stop the daemon from starting"


def test_the_unit_ships_where_packages_ship_units():
    """Debian Policy 9.3.2: /usr/lib/systemd/system, never /etc."""
    assert "usr/lib/systemd/system/gigactl.service" in installed_files()


@pytest.mark.parametrize("source,dest", [
    ("daemon/data/io.github.smairio.gigactl.conf", "usr/share/dbus-1/system.d"),
    ("daemon/data/io.github.smairio.gigactl.policy", "usr/share/polkit-1/actions"),
])
def test_system_policy_goes_to_the_package_directory_not_etc(source, dest):
    assert (source, dest) in install_lines()


# --- the launcher -----------------------------------------------------------

def test_the_launcher_runs_the_same_entry_points_as_the_pyprojects():
    """One script serves both binaries, so the .deb and a pip install have to
    agree on which module each name starts."""
    declared = {**pyproject_scripts("daemon/pyproject.toml"),
                **pyproject_scripts("gui/pyproject.toml")}
    assert declared, "expected [project.scripts] entry points to compare against"
    for name, target in declared.items():
        module, function = target.split(":")
        assert function == "main", target
        assert launcher_programs().get(name) == module.split(".")[0], name


def test_every_entry_point_name_is_a_symlink_to_the_one_launcher():
    installed = installed_files()
    launcher = f"{private_dir()}/launcher"
    assert launcher in installed
    for name in launcher_programs():
        assert installed.get(f"usr/bin/{name}") == f"symlink -> {launcher}", name


def test_the_launcher_puts_the_private_dir_on_the_path():
    """Python points sys.path[0] at the symlink's directory (/usr/bin), so the
    private directory has to be added explicitly."""
    assert f'PRIVATE_DIR = "/{private_dir()}"' in text("debian/bin/launcher")


def test_one_declaration_of_the_private_directory():
    """debian/rules declares it; the launcher and every install destination under
    it are checked against that, so moving it is a one-line change."""
    declared = private_dir()
    assert f"dh_python3 $(PRIVATE_DIR)" in text("debian/rules")
    module_dests = [dest for _s, dest in install_lines()
                    if "gigactl/gigactl" in dest or dest.endswith("/gigactl")]
    assert module_dests, "expected the module tree to be installed somewhere"
    for dest in module_dests:
        assert dest.startswith(declared), f"{dest} is not under {declared}"


@pytest.mark.parametrize("script", ["debian/bin/launcher", "debian/rules",
                                    "debian/preinst", "debian/postinst",
                                    "debian/postrm"])
def test_shipped_scripts_are_executable(script):
    assert (ROOT / script).stat().st_mode & stat.S_IXUSR, script


# --- icons ------------------------------------------------------------------

def test_every_generated_icon_is_both_built_and_installed():
    rules, installed = text("debian/rules"), installed_files()
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
    assert rules_variable("ICON_DIR") == BUILT_BY_RULES.rstrip("/")
    assert re.search(r"^override_dh_clean:\n\trm -rf \$\(ICON_DIR\)$",
                     text("debian/rules"), re.MULTILINE)


def test_the_symbolic_icon_is_monochrome():
    """A symbolic icon the theme can recolour: one flat fill, no gradients."""
    svg = text("design/icon-symbolic.svg")
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
    assert "nbfc" not in text("debian/control")


def test_build_depends_cover_what_rules_actually_runs():
    build = text("debian/control")
    for tool, package in [("rsvg-convert", "librsvg2-bin"),
                          ("dh_python3", "dh-python"),
                          ("desktop-file-validate", "desktop-file-utils")]:
        if tool in text("debian/rules"):
            assert package in build, f"{tool} needs Build-Depends: {package}"


def test_the_build_time_check_honours_nocheck():
    """An override body runs unconditionally, so DEB_BUILD_OPTIONS=nocheck has to
    be honoured by hand — Policy 4.9.1."""
    rules = text("debian/rules")
    guard = re.search(r"^override_dh_auto_test:\n(.*?)\nendif$", rules,
                      re.MULTILINE | re.DOTALL)
    assert guard, "the test override should be wrapped in a nocheck guard"
    assert "filter nocheck,$(DEB_BUILD_OPTIONS)" in guard.group(1)


def test_the_changelog_parses_and_names_this_package():
    assert re.fullmatch(r"\d+\.\d+\.\d+", changelog_version()), changelog_version()
    assert re.match(r"^gigactl \(", text("debian/changelog"))


@pytest.mark.parametrize("source", ["daemon/gigactld/__init__.py",
                                    "gui/gigactl_gui/__init__.py",
                                    "daemon/pyproject.toml", "gui/pyproject.toml"])
def test_one_version_everywhere(source):
    """The daemon publishes its ``__version__`` as the DaemonVersion property, so
    a changelog that disagrees ships a package whose own API misreports it."""
    packaged = changelog_version()
    assert f'"{packaged}"' in text(source), f"{source} disagrees with {packaged}"


def test_a_native_package_has_no_upstream_tarball():
    assert "3.0 (native)" in text("debian/source/format")


# --- maintainer scripts -----------------------------------------------------

@pytest.mark.parametrize("script", ["preinst", "postinst", "postrm"])
def test_maintainer_scripts_are_strict_and_leave_room_for_debhelper(script):
    body = text(f"debian/{script}")
    assert "set -e" in body
    assert "#DEBHELPER#" in body, "dh's own snippets have nowhere to go"


@pytest.mark.parametrize("script,known", [
    ("postinst", ["configure", "abort-upgrade", "abort-remove",
                  "abort-deconfigure", "triggered"]),
    ("postrm", ["purge", "remove", "upgrade", "failed-upgrade", "abort-install",
                "abort-upgrade", "disappear"]),
])
def test_maintainer_scripts_reject_an_argument_they_do_not_know(script, known):
    """dpkg grows new maintainer-script arguments; failing loudly on one beats
    silently doing nothing. Every argument dpkg passes today must still be
    accepted, or the package breaks on ordinary remove/upgrade."""
    body = text(f"debian/{script}")
    for argument in known:
        assert argument in body, f"{script} would reject dpkg's '{argument}'"
    assert re.search(r"^\s*\*\)\n", body, re.MULTILINE), "no catch-all arm"
    assert f"{script} called with unknown argument" in body
    assert "exit 1" in body


def test_preinst_guards_the_hardware_like_install_sh():
    body = text("debian/preinst")
    assert "sys_vendor" in body and "product_name" in body
    assert "GIGACTL_FORCE_INSTALL" in body, "there must be an escape hatch"


def test_the_hardware_guard_runs_on_install_only():
    """GIGACTL_FORCE_INSTALL is not persisted, and unattended-upgrades cannot
    supply it — so guarding upgrades too would strand a forced machine."""
    case = re.search(r"^case \"\$1\" in\n(.*?)^esac", text("debian/preinst"),
                     re.MULTILINE | re.DOTALL)
    assert case, "expected preinst to dispatch on $1"
    guarded = re.search(r"^\s*(\S+)\)\s*check_hardware", case.group(1), re.MULTILINE)
    assert guarded and guarded.group(1) == "install", case.group(1)


def test_postinst_supersedes_both_legacy_keyboard_restore_paths():
    """Issue #22: gkbd-restore.service and the sleep hook re-apply the backlight
    from /var/lib/gkbd, racing the daemon's own /var/lib/gigactl state."""
    body = text("debian/postinst")
    assert "gkbd-restore.service" in body
    assert "/lib/systemd/system-sleep/gkbd" in body
    assert "disable" in body


def test_postinst_notices_go_to_stderr():
    """They are diagnostics, and the rest of the tree keeps those off stdout."""
    for line in text("debian/postinst").splitlines():
        stripped = line.strip()
        if stripped.startswith("echo \"gigactl:"):
            assert ">&2" in line or line.rstrip().endswith("\\"), line


def test_purge_removes_the_daemon_state():
    body = text("debian/postrm")
    assert "purge" in body and "/var/lib/gigactl" in body


def test_the_state_directory_is_owned_by_the_package():
    assert "var/lib/gigactl" in text("debian/dirs")
