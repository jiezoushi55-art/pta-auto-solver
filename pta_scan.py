"""
PTA 作业扫描 —— 只扫描真正的作业

判定标准：作业必须带截止时间（"X 分钟后关闭" / "X 天后关闭" / "答题中"）
教材、练习题集一律不扫描
"""

import asyncio
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pta_core as core

DASHBOARD = "https://pintia.cn/problem-sets/dashboard"


def line(ch="─", n=62):
    print(ch * n)


# 只抓真正的作业：带截止时间的
JS_LIST_ASSIGNMENTS = """
() => {
  const out = [];
  const seen = new Set();

  // 策略1：找 "即将关闭的题目集" 分区里的链接
  // 策略2：卡片文本里带 "后关闭" / "答题中" 的

  document.querySelectorAll('a').forEach(a => {
    const href = a.getAttribute('href') || '';
    const m = href.match(/problem-sets\\/(\\d+)/);
    if (!m) return;
    const id = m[1];

    const linkText = (a.innerText || '').trim().replace(/\\n/g, ' ');
    if (!linkText) return;

    // 往上找卡片容器（最多 5 层）
    let card = a;
    for (let i = 0; i < 5 && card.parentElement; i++) {
      card = card.parentElement;
      if ((card.innerText || '').length > linkText.length + 10) break;
    }
    const cardText = (card.innerText || '').replace(/\\n/g, ' ');

    // 判定：必须有截止时间或答题状态
    const deadlineMatch = cardText.match(/(\\d+)\\s*(天|小时|分钟)\\s*后关闭/);
    const isAnswering = /答题中|进行中/.test(cardText);

    if (!deadlineMatch && !isAnswering) return;

    if (seen.has(id)) return;
    seen.add(id);

    out.push({
      id,
      name: linkText.slice(0, 80),
      deadline: deadlineMatch ? deadlineMatch[0] : null,
      answering: isAnswering
    });
  });

  return out;
}
"""


async def main():
    cfg = core.load_config()
    cdp = core.CDP(cfg.get("浏览器", {}).get("调试端口", 9222))

    print()
    line("═")
    print("  PTA 作业扫描")
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

    await cdp.goto(DASHBOARD, wait=9)
    st = await core.get_state(cdp)
    if st.get("isLogin"):
        print()
        print("  ❌ 未登录 PTA，请在浏览器里登录后重试")
        return 1
    print("  ✅ 已登录")

    # 只找真正的作业
    assignments = await cdp.js(JS_LIST_ASSIGNMENTS)

    if not assignments:
        print()
        line()
        print("  ✅ 当前没有进行中的作业")
        print("     （只有带截止时间的题目集才算作业）")
        line()
        print()
        await cdp.close()
        return 0

    print()
    line()
    print(f"  找到 {len(assignments)} 个进行中的作业")
    line()

    total_todo = 0

    for a in assignments:
        set_id = a["id"]
        url = f"https://pintia.cn/problem-sets/{set_id}/exam/problems/type/7"
        await cdp.goto(url, wait=9)

        s = await core.get_state(cdp)
        probs = await core.list_problems(cdp)

        print()
        name = a["name"]
        if len(name) > 55:
            name = name[:52] + "..."
        print(f"  📘 {name}")

        if a.get("deadline"):
            print(f"     剩余时间 : {a['deadline']}")
        elif s.get("deadline"):
            print(f"     剩余时间 : {s['deadline']}")

        if s.get("progress"):
            p = s["progress"]
            done, total = p["done"], p["total"]
            bar_len = 20
            filled = int(bar_len * done / total) if total else 0
            bar = "█" * filled + "░" * (bar_len - filled)
            print(f"     进度     : {bar}  {done}/{total}")
        else:
            print(f"     进度     : （读不到）")

        if probs:
            todo = [x for x in probs if x["status"] != "done"]
            done_p = [x for x in probs if x["status"] == "done"]
            total_todo += len(todo)

            if not todo:
                print(f"     状态     : ✅ 全部完成（{len(done_p)} 题）")
            else:
                print(f"     状态     : ⚠ 还有 {len(todo)} 题没做")

            # 详细列出每道题
            print()
            print(f"     {'题号':<6} {'标题':<24} {'分数':<5} {'状态':<8} 通过率")
            print(f"     {'-'*6} {'-'*24} {'-'*5} {'-'*8} {'-'*12}")
            for p in probs:
                st_map = {
                    "done": "✅ 已完成",
                    "failed": "❌ 未通过",
                    "todo": "⬜ 未做",
                    "unknown": "?",
                }
                st = st_map.get(p["status"], "?")
                title = p.get("title") or ""
                if len(title) > 22:
                    title = title[:21] + "…"
                num = p.get("num", "")
                if len(num) > 5:
                    num = num[:5]
                score = p.get("score", "") or "-"
                rate = p.get("rate", "") or ""
                if len(rate) > 12:
                    rate = rate[:12]
                print(f"     {num:<6} {title:<24} {score:<5} {st:<8} {rate}")
        else:
            print(f"     状态     : （没有编程题）")

    print()
    line("═")
    if total_todo == 0:
        print("  🎉 所有作业都完成了")
    else:
        print(f"  ⚠ 还有 {total_todo} 题没做")
    line("═")
    print()

    await cdp.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
