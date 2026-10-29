"""显示当前配置状态（给 bat 菜单用）"""
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pta_core as core

CFG = Path(__file__).parent / "config.json"


def main():
    try:
        cfg = core.load_config()
    except Exception as e:
        print(f"  配置文件读取失败: {e}")
        return

    key = (cfg.get("apiKey") or "").strip()
    key_status = "已填写" if key else "❌ 未填写"

    print(f"  API Key : {key_status}")
    print(f"  模型    : {cfg.get('model', '?')}")
    print(f"  接口    : {cfg.get('baseUrl', '?')}")

    # 编译器
    ans = cfg.get("答题设置", {})
    comp_key = ans.get("编译器") or "python3"
    comp_full = core.resolve_compiler(comp_key)
    comp_lang = core.compiler_language(comp_key)
    print(f"  编译器  : {comp_full}  ({comp_lang})")

    # 账号信息
    accounts = core.get_accounts(cfg)
    cur = core.get_current_account(cfg)

    if not accounts:
        print(f"  PTA账号 : ❌ 未添加（按 [4] 添加）")
    else:
        if len(accounts) == 1:
            a = accounts[0]
            acct = a["账号"]
            masked = acct[:3] + "***" + acct[-3:] if len(acct) > 6 else acct
            pwd_ok = "✓" if a["密码"] else "⚠无密码"
            print(f"  PTA账号 : {a['名字']}（{masked}） {pwd_ok}")
        else:
            acct = cur["账号"]
            masked = acct[:3] + "***" + acct[-3:] if len(acct) > 6 else acct
            print(f"  PTA账号 : {cur['名字']}（{masked}） 共 {len(accounts)} 个账号")


if __name__ == "__main__":
    main()
