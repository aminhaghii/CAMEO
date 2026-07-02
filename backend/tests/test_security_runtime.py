import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


def _route_decorators(function_name):
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            decorators = []
            for dec in node.decorator_list:
                if isinstance(dec, ast.Name):
                    decorators.append(dec.id)
                elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name):
                    decorators.append(dec.func.id)
                elif isinstance(dec, ast.Attribute):
                    decorators.append(dec.attr)
                elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                    decorators.append(dec.func.attr)
            return decorators
    raise AssertionError(f"Function {function_name} not found in app.py")


def test_runtime_debug_mode_is_env_gated():
    """The Flask debugger must not be hard-coded on in production entrypoint."""
    source = APP_PATH.read_text(encoding="utf-8")
    assert "app.run(debug=True" not in source
    assert "FLASK_DEBUG" in source


def test_reference_api_routes_have_explicit_auth_decorator():
    """Chemical reference APIs are useful but should not rely only on global middleware."""
    for function_name in ("get_reactivity_stats", "get_reactive_groups"):
        decorators = _route_decorators(function_name)
        assert "login_required" in decorators, (
            f"{function_name} should explicitly require login"
        )


def test_cors_does_not_allow_wildcard_with_credentials():
    """Credentialed CORS must remain origin allow-list based."""
    source = APP_PATH.read_text(encoding="utf-8")
    assert "supports_credentials=True" in source
    assert "origins='*'" not in source
    assert 'origins="*"' not in source
    assert "origins=[" in source

