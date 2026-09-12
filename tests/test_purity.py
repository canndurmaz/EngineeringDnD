"""engine/ must stay free of framework, database, and model dependencies.

Reading the JSON data files is allowed; importing Flask, sqlite3, llama_cpp, or the
bare stdlib RNG is not. That is what keeps the whole ruleset unit-testable in isolation.
"""
import ast
import pathlib

FORBIDDEN = {"flask", "sqlite3", "llama_cpp", "random", "os", "time", "requests"}


def _imported_roots(path):
    tree = ast.parse(path.read_text())
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _scan_engine():
    """Scan engine/ for forbidden imports, excluding engine/dice.py (sanctioned RNG seam)."""
    offenders = {}
    for path in pathlib.Path("engine").rglob("*.py"):
        if path.as_posix() == "engine/dice.py":
            continue  # the one sanctioned RNG seam, matched by full path
        bad = _imported_roots(path) & FORBIDDEN
        if bad:
            offenders[str(path)] = sorted(bad)
    return offenders


def test_engine_imports_no_io_modules():
    offenders = _scan_engine()
    assert offenders == {}, f"engine/ must stay pure, found: {offenders}"


def test_exemption_does_not_widen_to_other_dice_files():
    """Prove the dice.py exemption does not widen to nested dice.py files."""
    nested = pathlib.Path("engine/nested_probe")
    nested.mkdir(parents=True, exist_ok=True)
    probe = nested / "dice.py"
    probe.write_text("import os\n")
    try:
        offenders = _scan_engine()
        assert "engine/nested_probe/dice.py" in offenders, (
            "exemption widened silently: nested dice.py was not reported"
        )
    finally:
        probe.unlink()
        nested.rmdir()
