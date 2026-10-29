"""
PTA 自动登录 —— 用 config.json 里保存的账号密码自动登录

PTA 登录页结构：
  input[type=text]      placeholder="电子邮箱或手机号码"
  input[type=password]  placeholder="密码"
  button "登录"
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


# 检查是否已登录
JS_CHECK = """
() => {
  try {
    const raw = localStorage.getItem('user-cache');
    if (raw) {
      const u = JSON.parse(raw);
      if (u && u.userId) return { logged: true, userId: u.userId };
    }
  } catch (e) {}
  const onLogin = location.href.includes('/auth/') ||
                  !!document.querySelector('input[type="password"]');
  return { logged: false, onLoginPage: onLogin, url: location.href };
}
"""

# 自动填表并提交（React 受控组件正确写法）
JS_FILL = """
(args) => {
  const { account, password } = args;

  // React 受控输入必须用原生 setter + input 事件
  function setNativeValue(el, value) {
    const proto = Object.getPrototypeOf(el);
    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
    if (desc && desc.set) {
      desc.set.call(el, value);
    } else {
      el.value = value;
    }
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  // 找账号框
  const inputs = Array.from(document.querySelectorAll('input'));
  const accInput = inputs.find(i =>
    i.type === 'text' || i.type === 'email' || i.type === 'tel'
  );
  const pwdInput = inputs.find(i => i.type === 'password');

  if (!accInput || !pwdInput) {
    return { ok: false, reason: '找不到账号或密码输入框', count: inputs.length };
  }

  setNativeValue(accInput, account);
  setNativeValue(pwdInput, password);

  // 找登录按钮（排除"注册"等）
  const btns = Array.from(document.querySelectorAll('button'));
  const loginBtn = btns.find(b => {
    const t = (b.innerText || '').trim();
    return t === '登录' || /^登\s*录$/.test(t);
  });

  if (!loginBtn) {
    return { ok: false, reason: '找不到登录按钮', buttons: btns.map(b => (b.innerText||'').trim()).filter(Boolean) };
  }

  loginBtn.click();

  return {
    ok: true,
    accountFilled: accInput.value === account,
    passwordFilled: pwdInput.value.length === password.length
  };
}
"""

# 检测异常（滑块、验证码、错误提示）
JS_DETECT_BLOCK = """
() => {
  const body = document.body.innerText || '';
  const html = document.documentElement.innerHTML || '';

  const out = { blocked: false, reason: '', needCaptcha: false };

  // 滑块验证码
  if (/drag|slider|captcha|验证码|滑块|拖动/.test(body) &&
      document.querySelector('[class*="captcha"], [class*="slider"], [class*="verify"]')) {
    out.needCaptcha = true;
    out.blocked = true;
    out.reason = '需要完成人机验证（滑块/验证码）';
  }

  // 错误提示
  const errEl = document.querySelector('[class*="error"], [class*="danger"], [role="alert"]');
  if (errEl) {
    const t = (errEl.innerText || '').trim();
    if (t && t.length < 60) {
      out.blocked = true;
      out.reason = '登录失败: ' + t;
    }
  }

  return out;
}
"""


async def is_logged_in(cdp):
    try:
        r = await cdp.js(JS_CHECK)
        return bool(r and r.get("logged")), (r or {}).get("userId")
    except Exception:
        return False, None


async def auto_login(cdp, account, password, captcha_wait=90):
    """
    自动登录 PTA
    返回 {ok, reason, userId}
    """
    print("  打开登录页…")
    await cdp.goto(LOGIN_URL, wait=6)

    # 检查是否已经在登录页
    try:
        r = await cdp.js(JS_CHECK)
        if r and r.get("logged"):
            return {"ok": True, "reason": "已经登录", "userId": r.get("userId")}
    except Exception:
        pass

    # 填表
    print("  填写账号密码…")
    try:
        fill = await cdp.js(JS_FILL, {"account": account, "password": password})
    except Exception as e:
        return {"ok": False, "reason": f"填表异常: {e}"}

    if not (isinstance(fill, dict) and fill.get("ok")):
        return {"ok": False, "reason": (fill or {}).get("reason", "填表失败")}

    print(f"  已填写（账号:{fill.get('accountFilled')} 密码:{fill.get('passwordFilled')}）")
    print("  已点击登录，等待响应…")

    # 轮询登录结果
    elapsed = 0
    asked_captcha = False

    while elapsed < captcha_wait:
        await asyncio.sleep(2)
        elapsed += 2

        # 已登录？
        logged, uid = await is_logged_in(cdp)
        if logged:
            return {"ok": True, "reason": "登录成功", "userId": uid}

        # 有阻碍？
        try:
            blk = await cdp.js(JS_DETECT_BLOCK)
        except Exception:
            blk = None

        if isinstance(blk, dict) and blk.get("blocked"):
            reason = blk.get("reason", "")

            if blk.get("needCaptcha"):
                if not asked_captcha:
                    print()
                    print("  ⚠ 需要人机验证（滑块/验证码）")
                    print("     请在浏览器窗口里手动完成验证")
                    print(f"     完成后会自动继续（最多等 {captcha_wait} 秒）")
                    asked_captcha = True
                # 继续等
            else:
                # 账号密码错误之类的硬失败
                return {"ok": False, "reason": reason}

        if elapsed % 10 == 0:
            print(f"     …等待中（{elapsed}s）")

    return {"ok": False, "reason": "超时未登录成功"}


async def ensure_login(cdp, cfg, interactive=True):
    """
    确保处于登录状态：已登录直接返回；未登录则尝试自动登录
    返回 (ok, message)
    """
    logged, uid = await is_logged_in(cdp)
    if logged:
        return True, f"已登录（userId {uid}）"

    pta = cfg.get("pta", {})
    auto = pta.get("自动登录", True)
    wait_sec = int(pta.get("等待滑块秒数", 90) or 90)

    # 取当前选中的账号
    acc = core.get_current_account(cfg)
    account = acc["账号"] if acc else ""
    password = acc["密码"] if acc else ""
    acc_name = acc["名字"] if acc else ""

    # 没配账号密码 → 只能手动
    if not account or not password:
        if not interactive:
            return False, "未登录，且没有可用的 PTA 账号（请先在菜单 [4] 设置）"
        print()
        print("  ⚠ 未登录 PTA，且没有保存的账号密码")
        print("     请按菜单 [4] 设置 PTA 账号密码")
        print("     或者在弹出的浏览器窗口里手动登录")
        print("     登录完成后按回车继续…")
        await cdp.goto(LOGIN_URL, wait=4)
        input("     按回车继续 > ")
        await cdp.goto(DASHBOARD, wait=6)
        logged, uid = await is_logged_in(cdp)
        if logged:
            return True, f"登录成功（userId {uid}）"
        return False, "仍未登录"

    # 配了账号密码 → 自动登录
    if not auto:
        if not interactive:
            return False, "未登录（自动登录已关闭）"
        print()
        print("  ⚠ 未登录，自动登录已关闭")
        print("     请手动登录后按回车…")
        await cdp.goto(LOGIN_URL, wait=4)
        input("     按回车继续 > ")
        await cdp.goto(DASHBOARD, wait=6)
        logged, uid = await is_logged_in(cdp)
        return (logged, f"userId {uid}" if logged else "仍未登录")

    print()
    print("  检测到未登录，正在自动登录…")
    print(f"  账号: {acc_name}（{account[:3]}***）")
    r = await auto_login(cdp, account, password, captcha_wait=wait_sec)

    if r.get("ok"):
        return True, f"登录成功（{acc_name}，userId {r.get('userId')}）"
    return False, r.get("reason", "登录失败")


async def switch_account(cdp, cfg, target_name=None):
    """
    切换账号：退出当前 → 登录目标账号
    target_name 为 None 时用 config 里"当前账号"
    返回 (ok, message)
    """
    accounts = core.get_accounts(cfg)
    if not accounts:
        return False, "没有保存任何账号，请先在菜单 [4] 添加"

    # 确定目标账号
    if target_name:
        target = next((a for a in accounts if a["名字"] == target_name), None)
    else:
        target = core.get_current_account(cfg)

    if not target:
        return False, f"找不到账号「{target_name}」"

    pta = cfg.get("pta", {})
    wait_sec = int(pta.get("等待滑块秒数", 90) or 90)

    print()
    print(f"  切换到账号: {target['名字']}")

    # 1) 先退出当前
    logged, uid = await is_logged_in(cdp)
    if logged:
        print(f"  退出当前账号（userId {uid}）…")
        await cdp.js(JS_LOGOUT)
        await asyncio.sleep(1)
        await cdp.goto(DASHBOARD, wait=5)

    # 2) 登录目标
    print(f"  登录 {target['名字']}…")
    r = await auto_login(cdp, target["账号"], target["密码"], captcha_wait=wait_sec)

    if r.get("ok"):
        return True, f"已切换到「{target['名字']}」（userId {r.get('userId')}）"
    return False, f"切换失败: {r.get('reason')}"


# 退出登录用的 JS（从 pta_account 挪过来，避免循环导入）
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


async def main():
    """独立运行：测试自动登录"""
    cfg = core.load_config()
    cdp = core.CDP(cfg.get("浏览器", {}).get("调试端口", 9222))

    print()
    print("=" * 62)
    print("  PTA 自动登录测试")
    print("=" * 62)

    if not cdp.list_pages():
        print("\n  ❌ 连不上调试浏览器")
        return 1

    print("\n  连接浏览器…")
    await cdp.connect()
    print("  ✅ 已连接")

    ok, msg = await ensure_login(cdp, cfg)
    print()
    print(f"  {'✅' if ok else '❌'} {msg}")
    print()

    await cdp.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
