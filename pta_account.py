"""
PTA 账号状态 —— 查看当前登录、切换账号、退出登录、测试自动登录
"""

import asyncio
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pta_core as core

LOGIN_URL = "https://pintia.cn/auth/login"
DASHBOARD = "https://pintia.cn/problem-sets/dashboard"


def line(ch="─", n=62):
    print(ch * n)


JS_ACCOUNT = """
() => {
  const out = { logged: false, userId: null, onLoginPage: false };
  try {
    const raw = localStorage.getItem('user-cache');
    if (raw) {
      const u = JSON.parse(raw);
      if (u && u.userId) { out.logged = true; out.userId = u.userId; }
    }
  } catch (e) {}
  out.onLoginPage = location.href.includes('/auth/') ||
                    !!document.querySelector('input[type="password"]');
  out.url = location.href;
  return out;
}
"""

JS_LOGOUT = """
() => {
  const lsKeys = [];
  for (let i = 0; i < localStorage.length; i++) lsKeys.push(localStorage.key(i));
  lsKeys.forEach(k => { try { localStorage.removeItem(k); } catch (e) {} });

  const ssKeys = [];
  for (let i = 0; i < sessionStorage.length; i++) ssKeys.push(sessionStorage.key(i));
  ssKeys.forEach(k => { try { sessionStorage.removeItem(k); } catch (e) {} });

  document.cookie.split(';').forEach(c => {
    const name = c.split('=')[0].trim();
    if (!name) return;
    document.cookie = name + '=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
    document.cookie = name + '=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/; domain=.pintia.cn';
  });

  return { lsCleared: lsKeys.length, ssCleared: ssKeys.length };
}
"""


async def show_status(cdp, cfg):
    """显示浏览器登录状态 + 配置里的账号列表"""
    await cdp.goto(DASHBOARD, wait=7)
    live = await cdp.js(JS_ACCOUNT)

    accounts = core.get_accounts(cfg)
    cur = core.get_current_account(cfg)

    print()
    line()
    print("  当前状态")
    line()

    # 浏览器里的实际登录
    if live.get("logged"):
        print(f"  浏览器登录 : ✅ 已登录")
        print(f"  userId     : {live.get('userId')}")
    else:
        print(f"  浏览器登录 : ❌ 未登录")

    # 配置里的账号
    if not accounts:
        print(f"  已存账号   : 无（请用菜单 [4] 添加）")
    else:
        print(f"  已存账号   : {len(accounts)} 个")
        for a in accounts:
            mark = "★" if cur and a["名字"] == cur["名字"] else " "
            acct = a["账号"]
            masked = acct[:3] + "***" + acct[-3:] if len(acct) > 6 else acct
            pwd_info = f"{len(a['密码'])}位" if a["密码"] else "⚠无密码"
            print(f"    {mark} {a['名字']:<14} {masked:<22} 密码{pwd_info}")

    print()
    return live, accounts, cur


async def do_switch(cdp, cfg, accounts, cur):
    """切换账号"""
    if not accounts:
        print()
        print("  还没有保存账号，请先用菜单 [4] 添加")
        return False

    if len(accounts) == 1:
        target = accounts[0]
        print()
        print(f"  只有一个账号「{target['名字']}」，直接登录它")
    else:
        print()
        for i, a in enumerate(accounts, 1):
            mark = "★" if cur and a["名字"] == cur["名字"] else " "
            print(f"  {mark} [{i}] {a['名字']}")
        print()
        try:
            raw = input(f"  切换到哪个？[1-{len(accounts)}] > ").strip()
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
        target = accounts[idx]

    # 记住选择
    core.set_accounts(cfg, accounts, target["名字"])
    cfg2 = core.load_config()

    import pta_login
    ok, msg = await pta_login.switch_account(cdp, cfg2, target["名字"])
    print()
    print(f"  {'✅' if ok else '❌'} {msg}")
    return ok


async def do_logout(cdp):
    """退出登录"""
    print()
    live = await cdp.js(JS_ACCOUNT)
    if not live.get("logged"):
        print("  当前本来就没登录")
        return True

    print(f"  当前 userId: {live.get('userId')}")
    print()
    print("  ⚠ 会清除浏览器里的 PTA 登录状态")
    print()
    try:
        confirm = input("  确认退出？(y/N) > ").strip().lower()
    except EOFError:
        confirm = "n"
    if confirm != "y":
        print("  已取消")
        return False

    r = await cdp.js(JS_LOGOUT)
    print(f"  已清除 {r.get('lsCleared', 0)} 项本地存储")
    await cdp.goto(DASHBOARD, wait=5)
    live2 = await cdp.js(JS_ACCOUNT)
    if not live2.get("logged"):
        print("  ✅ 已退出")
    else:
        print("  ⚠ 本地已清，但服务端可能还记得会话")
    return True


async def do_test_autologin(cdp, cfg, cur):
    """测试自动登录"""
    if not cur:
        print()
        print("  没有保存账号，请先用菜单 [4] 添加")
        return False

    print()
    print(f"  测试账号「{cur['名字']}」的自动登录")
    print()

    # 先退出，制造未登录状态
    live = await cdp.js(JS_ACCOUNT)
    if live.get("logged"):
        print("  先退出当前登录…")
        await cdp.js(JS_LOGOUT)
        await asyncio.sleep(1)

    import pta_login
    pta = cfg.get("pta", {})
    wait_sec = int(pta.get("等待滑块秒数", 90) or 90)

    print("  正在自动登录…")
    r = await pta_login.auto_login(cdp, cur["账号"], cur["密码"], captcha_wait=wait_sec)

    print()
    if r.get("ok"):
        print(f"  ✅ 自动登录成功！userId: {r.get('userId')}")
        return True
    print(f"  ❌ 自动登录失败: {r.get('reason')}")
    return False


async def main():
    cfg = core.load_config()
    cdp = core.CDP(cfg.get("浏览器", {}).get("调试端口", 9222))

    print()
    line("═")
    print("  PTA 账号状态")
    line("═")

    pages = cdp.list_pages()
    if not pages:
        print()
        print("  ❌ 连不上调试浏览器（端口 9222）")
        print(r"     请先启动: D:\ChromeDebug\启动Chrome调试版.bat")
        print()
        return 1

    print()
    print("  连接浏览器…")
    try:
        await cdp.connect()
    except Exception as e:
        print(f"  ❌ {e}")
        return 1
    print("  ✅ 已连接")

    live, accounts, cur = await show_status(cdp, cfg)

    print("  [1] 切换账号（退出当前 → 登录选中的）")
    print("  [2] 退出当前登录")
    print("  [3] 测试自动登录（会先退出再登）")
    print("  [0] 返回")
    print()

    try:
        ch = input("  请选择 > ").strip()
    except EOFError:
        ch = "0"

    if ch == "1":
        await do_switch(cdp, cfg, accounts, cur)
    elif ch == "2":
        await do_logout(cdp)
    elif ch == "3":
        await do_test_autologin(cdp, cfg, cur)

    print()
    await cdp.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
