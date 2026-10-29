"""
PTA 编译器设置 —— 选择答题用哪种语言

只支持四种：Python3 / C / C++ / Java
"""

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pta_core as core


def line(ch="─", n=62):
    print(ch * n)


# 支持的四种语言
COMPILER_OPTIONS = [
    ("python3", "Python (python3)", "Python3", "python", "Python 解释器"),
    ("c",       "C (gcc)",          "C",       "c",      "gcc"),
    ("cpp",     "C++ (g++)",        "C++",     "cpp",    "g++"),
    ("java",    "Java (javac)",     "Java",    "java",   "javac"),
]


def show_current(cfg):
    ans = cfg.get("答题设置", {})
    current = ans.get("编译器") or "python3"
    full = core.resolve_compiler(current)
    lang = core.compiler_language(current)

    print()
    line()
    print("  当前编译器")
    line()
    print(f"    设置   : {current}")
    print(f"    PTA 名 : {full}")
    print(f"    语言   : {lang}")
    print()


def pick_compiler(cfg):
    show_current(cfg)

    print("  可选：")
    for i, (key, full, desc, lang, tool) in enumerate(COMPILER_OPTIONS, 1):
        # 检查本机是否有对应编译器
        from shutil import which
        has = bool(which(tool)) if lang != "python" else True
        mark = "✅" if has else "⚠"
        print(f"    [{i}] {desc:<10} {full:<20} {mark} 本地{('有 ' + tool) if has else ('无 ' + tool + '，无法本地验证')}")
    print()
    print("     [0] 返回")
    print()

    try:
        raw = input("  请选择 > ").strip()
    except EOFError:
        return False

    if not raw or raw == "0":
        return False

    # 支持输编号或直接输语言名
    try:
        idx = int(raw)
        if not (1 <= idx <= len(COMPILER_OPTIONS)):
            print("  编号无效")
            return False
        key, full, desc, lang, tool = COMPILER_OPTIONS[idx - 1]
    except ValueError:
        # 输名字
        raw_l = raw.lower()
        matched = None
        for key, full, desc, lang, tool in COMPILER_OPTIONS:
            if raw_l == key or raw_l == lang or raw_l == desc.lower():
                matched = (key, full, desc, lang, tool)
                break
        if not matched:
            print(f"  只支持: python3 / c / cpp / java")
            return False
        key, full, desc, lang, tool = matched

    # 写入配置
    cfg.setdefault("答题设置", {})
    cfg["答题设置"]["编译器"] = key
    cfg["答题设置"]["语言"] = lang
    core.save_config(cfg)

    print()
    print(f"  ✅ 已设为 {desc}（{full}）")

    # 本地编译器检查
    from shutil import which
    if lang != "python" and not which(tool):
        print()
        print(f"  ⚠ 本机没有 {tool}，生成的代码没法本地跑样例验证")
        print(f"     会直接提交（风险自负）")
    elif lang != "python":
        print(f"     ✅ 本机有 {tool}，可以本地验证样例")

    return True


def main():
    cfg = core.load_config()

    while True:
        changed = pick_compiler(cfg)
        if not changed:
            break
        cfg = core.load_config()
        print()
        try:
            cont = input("  继续修改？(Y/n) > ").strip().lower()
        except EOFError:
            break
        if cont and cont != "y":
            break

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
