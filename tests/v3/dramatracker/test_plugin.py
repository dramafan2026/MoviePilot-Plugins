# 追剧助手 · 主类结构自检（无宿主环境下的静态合同校验）
# 真正的加载验证必须发生在真实 MoviePilot V3 宿主中；
# 本测试用 AST 检查主类合同完整性，不导入宿主 SDK。

import json
import ast
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[3] / "plugins.v3" / "dramatracker"
MAIN = PLUGIN_DIR / "__init__.py"


def _tree():
    return ast.parse(MAIN.read_text(encoding="utf-8"))


def _class_attrs(tree):
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "DramaTracker")
    attrs = {}
    for n in cls.body:
        value = None
        targets = []
        if isinstance(n, ast.Assign):
            targets, value = n.targets, n.value
        elif isinstance(n, ast.AnnAssign) and n.value is not None:
            targets, value = [n.target], n.value
        if isinstance(value, ast.Constant):
            for t in targets:
                if isinstance(t, ast.Name):
                    attrs[t.id] = value.value
    return cls, attrs


def test_class_and_metadata():
    _, attrs = _class_attrs(_tree())
    assert attrs["plugin_name"] == "追剧助手"
    assert attrs["plugin_version"] == "1.0.0"
    assert attrs["plugin_config_prefix"] == "dramatracker_"
    assert attrs["plugin_icon"] == "DramaTracker.png"


def test_required_methods():
    cls, _ = _class_attrs(_tree())
    methods = {n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    # 必实现合同
    for m in ["init_plugin", "get_state", "get_form", "get_page", "get_api", "stop_service"]:
        assert m in methods, f"缺少必实现方法 {m}"
    # 扩展能力
    for m in ["get_service", "get_command", "get_dashboard", "get_dashboard_meta",
              "get_actions", "get_agent_tools"]:
        assert m in methods, f"缺少扩展方法 {m}"


def test_package_metadata_consistent():
    pkg = json.loads((PLUGIN_DIR.parents[1] / "package.v3.json").read_text(encoding="utf-8"))
    assert "DramaTracker" in pkg
    assert pkg["DramaTracker"]["version"] == "1.0.0"
    assert pkg["DramaTracker"]["system_version"] == ">=3.0.0"
    _, attrs = _class_attrs(_tree())
    assert attrs["plugin_version"] == pkg["DramaTracker"]["version"]


def test_no_import_side_effects():
    """导入期不启动任务/不访问网络：顶层语句只允许 import/类定义/函数/常量赋值。"""
    tree = _tree()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef, ast.FunctionDef, ast.Assign, ast.AnnAssign)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):  # 模块 docstring
            continue
        raise AssertionError(f"顶层存在可疑副作用语句: {ast.dump(node)[:80]}")


def test_icon_exists():
    assert (PLUGIN_DIR / "DramaTracker.png").exists()
