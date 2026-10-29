"""测试模型连接"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pta_core as core
import pta_ai as ai


def main():
    cfg = core.load_config()

    print()
    print("=" * 60)
    print("  模型连接测试")
    print("=" * 60)
    print()
    print(f"  接口: {cfg['baseUrl']}")
    print(f"  模型: {cfg['model']}")
    key = cfg.get("apiKey", "")
    print(f"  Key : {'已填写 (' + key[:8] + '...)' if key else '❌ 未填写'}")
    print()

    if not key:
        print("  请先在 config.json 里填写 apiKey")
        return 1

    print("  正在测试…")
    ok, msg = ai.test_connection(cfg)
    if ok:
        print(f"  ✅ {msg}")
        return 0
    else:
        print(f"  ❌ {msg}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
