"""
PTA 账号设置 —— 管理多个账号，选择当前使用哪个

存储结构（config.json）：
  "pta": {
    "账号列表": [
      {"名字": "主号", "账号": "xxx", "密码": "***"},
      {"名字": "小号", "账号": "yyy", "密码": "***"}
    ],
    "当前账号": "主号",
    "自动登录": true,
    "等待滑块秒数": 90
  }

PWA 登录态在浏览器 cookie 里，同一浏览器同时只能登录一个账号。
切换账号 = 退出当前 → 登录选中的那个（约 10-20 秒，可能要过滑块）。
"""

import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pta_core as core

CFG = Path(__file__).parent / "config.json"


def line(ch="─", n=62):
    print(ch * n)


def mask(s, keep=3):
    if not s:
        return "（空）"
    if len(s) <= keep * 2:
        return "*" * len(s)
    return s[:keep] + "*" * (len(s) - keep * 2) + s[-keep:]


def show_list(cfg):
    accounts = core.get_accounts(cfg)
    cur = core.get_current_account(cfg)
    cur_name = cur["名字"] if cur else None

    print()
    line()
    print("  PTA 账号列表")
    line()

    if not accounts:
        print()
        print("    （还没有保存任何账号）")
        print()
        return accounts, cur

    print()
    for i, a in enumerate(accounts, 1):
        mark = "★" if a["名字"] == cur_name else " "
        pwd_len = len(a["密码"])
        pwd_info = f"密码 {pwd_len} 位" if pwd_len else "⚠ 无密码"
        print(f"  {mark} [{i}] {a['名字']:<16} {mask(a['账号']):<24} {pwd_info}")

    print()
    if cur:
        print(f"  当前使用: ★ {cur['名字']}")
    print()
    return accounts, cur


def add_account(cfg):
    print()
    line()
    print("  添加账号")
    line()
    print()
    print("  给这个账号起个名字（方便区分，比如「主号」「小号」）")
    print("  直接回车 = 用账号本身当名字")
    print()
    try:
        name = input("  名字 > ").strip()
    except EOFError:
        return False

    print()
    try:
        acc = input("  账号（邮箱或手机号）> ").strip()
    except EOFError:
        return False
    if not acc:
        print("  已取消")
        return False

    print()
    print("  密码（输入时不显示）")
    try:
        import getpass
        pwd = getpass.getpass("  密码 > ")
    except Exception:
        pwd = input("  密码 > ").strip()

    if not pwd:
        print("  已取消")
        return False

    accounts = core.get_accounts(cfg)

    # 检查重复
    for a in accounts:
        if a["账号"] == acc:
            print()
            print(f"  ⚠ 这个账号已经存在了（名字：{a['名字']}）")
            print("     要改密码？请在列表里编辑它")
            return False

    if not name:
        name = acc

    # 名字重复就加后缀
    base = name
    n = 2
    while any(a["名字"] == name for a in accounts):
        name = f"{base}{n}"
        n += 1

    accounts.append({"名字": name, "账号": acc, "密码": pwd})

    # 第一个账号自动设为当前
    cur_name = (core.get_current_account(cfg) or {}).get("名字")
    if not cur_name:
        cur_name = name

    core.set_accounts(cfg, accounts, cur_name)

    print()
    print(f"  ✅ 已添加账号「{name}」")
    if cur_name == name:
        print(f"     并设为当前使用账号")
    return True


def edit_account(cfg):
    accounts = core.get_accounts(cfg)
    if not accounts:
        print()
        print("  还没有账号，先添加一个")
        return False

    print()
    try:
        raw = input(f"  要编辑哪个？[1-{len(accounts)}] > ").strip()
    except EOFError:
        return False
    try:
        idx = int(raw) - 1
        if not (0 <= idx < len(accounts)):
            print("  编号无效")
            return False
    except ValueError:
        print("  编号无效")
        return False

    a = accounts[idx]

    print()
    print(f"  正在编辑「{a['名字']}」")
    print("  直接回车 = 保持原值")
    print()

    try:
        name = input(f"  名字 [{a['名字']}] > ").strip()
        acc = input(f"  账号 [{mask(a['账号'])}] > ").strip()
    except EOFError:
        return False

    print()
    print("  新密码（输入时不显示，直接回车 = 不改）")
    try:
        import getpass
        pwd = getpass.getpass("  密码 > ")
    except Exception:
        pwd = input("  密码 > ").strip()

    if name:
        a["名字"] = name
    if acc:
        a["账号"] = acc
    if pwd:
        a["密码"] = pwd

    cur = core.get_current_account(cfg)
    cur_name = cur["名字"] if cur else a["名字"]

    # 如果改了名字，同步当前账号名
    if cur and cur["账号"] == a["账号"]:
        cur_name = a["名字"]

    core.set_accounts(cfg, accounts, cur_name)

    print()
    print("  ✅ 已保存")
    return True


def delete_account(cfg):
    accounts = core.get_accounts(cfg)
    if not accounts:
        print()
        print("  还没有账号")
        return False

    print()
    try:
        raw = input(f"  要删除哪个？[1-{len(accounts)}] > ").strip()
    except EOFError:
        return False
    try:
        idx = int(raw) - 1
        if not (0 <= idx < len(accounts)):
            print("  编号无效")
            return False
    except ValueError:
        print("  编号无效")
        return False

    a = accounts[idx]

    print()
    print(f"  ⚠ 确认删除账号「{a['名字']}」（{mask(a['账号'])}）？")
    try:
        confirm = input("  确认？(y/N) > ").strip().lower()
    except EOFError:
        confirm = "n"
    if confirm != "y":
        print("  已取消")
        return False

    accounts.pop(idx)

    # 当前账号被删了，换成第一个
    cur = core.get_current_account(cfg)
    if cur and cur["账号"] == a["账号"]:
        cur_name = accounts[0]["名字"] if accounts else ""
    else:
        cur_name = cur["名字"] if cur else ""

    core.set_accounts(cfg, accounts, cur_name)

    print()
    print("  ✅ 已删除")
    return True


def select_account(cfg):
    accounts = core.get_accounts(cfg)
    if not accounts:
        print()
        print("  还没有账号，先添加一个")
        return False

    print()
    try:
        raw = input(f"  用哪个账号？[1-{len(accounts)}] > ").strip()
    except EOFError:
        return False
    try:
        idx = int(raw) - 1
        if not (0 <= idx < len(accounts)):
            print("  编号无效")
            return False
    except ValueError:
        print("  编号无效")
        return False

    a = accounts[idx]
    core.set_accounts(cfg, accounts, a["名字"])

    print()
    print(f"  ✅ 已切换到「{a['名字']}」")
    print()
    print("     注意：浏览器里现在可能还是别的账号")
    print("     下次运行答题时会自动退出并登录这个账号")
    return True


def main():
    cfg = core.load_config()

    while True:
        print()
        line("═")
        print("  PTA 账号设置")
        line("═")

        accounts, cur = show_list(cfg)

        print("  [1] 添加账号")
        if accounts:
            print("  [2] 编辑账号")
            print("  [3] 删除账号")
            print("  [4] 切换当前账号")
        print("  [0] 返回")
        print()

        try:
            ch = input("  请选择 > ").strip()
        except EOFError:
            break

        if ch == "1":
            add_account(cfg)
            cfg = core.load_config()
        elif ch == "2" and accounts:
            edit_account(cfg)
            cfg = core.load_config()
        elif ch == "3" and accounts:
            delete_account(cfg)
            cfg = core.load_config()
        elif ch == "4" and accounts:
            select_account(cfg)
            cfg = core.load_config()
        elif ch == "0" or ch == "":
            break

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
