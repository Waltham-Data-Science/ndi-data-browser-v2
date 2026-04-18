"""ADR-009 enforcement: services other than ontology_service must not import httpx."""
import ast
import pathlib


def test_no_httpx_in_services_except_ontology() -> None:
    services_dir = pathlib.Path(__file__).parent.parent.parent / "services"
    violators = []
    for py in services_dir.glob("*.py"):
        if py.name == "ontology_service.py":
            continue
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [n.name for n in getattr(node, "names", [])]
                mod = getattr(node, "module", None)
                if "httpx" in names or mod == "httpx":
                    violators.append(py.name)
    assert not violators, f"Services must not import httpx (per ADR-009): {violators}"
