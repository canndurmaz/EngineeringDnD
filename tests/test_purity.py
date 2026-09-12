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


def test_engine_imports_no_io_modules():
    offenders = {}
    for path in pathlib.Path("engine").rglob("*.py"):
        if path.name == "dice.py":
            continue  # the one sanctioned RNG seam
        bad = _imported_roots(path) & FORBIDDEN
        if bad:
            offenders[str(path)] = sorted(bad)
    assert offenders == {}, f"engine/ must stay pure, found: {offenders}"
