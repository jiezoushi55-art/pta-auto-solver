"""
PTA 一键答题 —— 主程序

流程：检查登录 → 扫描作业 → 显示未做题 → 生成答案 → 验证 → 提交 → 报分数
"""

import asyncio
import json
import sys
import time
from pathlib import Path

# 让 emoji / 中文在 Windows 控制台正常显示
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pta_core as core
import pta_ai as ai

ROOT = Path(__file__).parent
DASHBOARD = "https://pintia.cn/problem-sets/dashboard"


def line(char="─", n=64):
    print(char * n)


def title(text):
    print()
    line("=")
    print(f"  {text}")
    line("=")


def ok(msg):
    print(f"  ✅ {msg}")


def bad(msg):
    print(f"  ❌ {msg}")


def info(msg):
    print(f"  ·  {msg}")


# ============ 步骤 1：检查配置 ============
def check_config(cfg):
    if not cfg.get("apiKey", "").strip():
        print()
        line("=")
        print("  ⚠️  还没有配置 API Key")
        line("=")
        print()
        print("  请打开这个文件填写：")
        print(f"  {ROOT / 'config.json'}")
        print()
        print("  需要填三项：")
        print("    baseUrl : 接口地址，例如 https://api.deepseek.com/v1")
        print("    apiKey  : 你的 Key")
        print("    model   : 模型名，例如 deepseek-chat")
        print()
        print("  填好后重新运行本程序。")
        print()
        return False
    return True


# ============ 步骤 2：连接浏览器 & 检查登录 ============
async def connect_and_login(cdp, cfg):
    title("步骤 1/5  连接浏览器")

    port = cfg.get("浏览器", {}).get("调试端口", 9222)
    cdp.port = port

    pages = cdp.list_pages()
    if not pages:
        bad(f"连不上调试浏览器（端口 {port}）")
        print()
        print("  请先启动调试浏览器：")
        print(r"  D:\ChromeDebug\启动Chrome调试版.bat")
        print()
        return False

    ok(f"浏览器已连接（{len(pages)} 个标签页）")

    try:
        await cdp.connect()
    except Exception as e:
        bad(f"连接失败: {e}")
        return False

    title("步骤 2/5  检查登录状态")

    import pta_login
    good, msg = await pta_login.ensure_login(cdp, cfg, interactive=True)
    if good:
        ok(msg)
        # 回到 dashboard 准备扫描
        await cdp.goto("https://pintia.cn/problem-sets/dashboard", wait=8)
        return True
    else:
        bad(msg)
        return False


def urllib_quote(s):
    import urllib.parse
    return urllib.parse.quote(s, safe="")


# ============ 步骤 3：扫描作业 ============
async def scan_assignments(cdp):
    title("步骤 3/5  扫描作业")

    # 先回 dashboard 看有哪些作业
    await cdp.goto(DASHBOARD, wait=8)

    data = await cdp.js("""
    () => {
      const out = [];
      const seen = new Set();
      document.querySelectorAll('a').forEach(a => {
        const h = a.getAttribute('href') || '';
        const m = h.match(/problem-sets\\/(\\d+)/);
        if (!m) return;
        const id = m[1];
        if (seen.has(id)) return;
        const t = (a.innerText||'').trim().replace(/\\n/g,' | ');
        if (!t) return;
        if (/^(开始答题|购书答题|继续答题)$/.test(t)) return;
        seen.add(id);
        let kind = 'other';
        if (/后关闭|答题中|进行中|已开放/.test(t)) kind = 'active';
        out.push({ id, text: t.slice(0, 120), kind });
      });
      return out;
    }
    """)

    if not data:
        bad("没找到任何题目集")
        return None

    # 只保留「真正的作业」= 带截止时间的
    active = [x for x in data if x["kind"] == "active"]

    if not active:
        print()
        bad("当前没有进行中的作业")
        print("     （只有带截止时间的题目集才算作业）")
        print("     教材/练习题集不在扫描范围内")
        return None

    show = active

    print()
    if len(active) == 1:
        print(f"  【进行中的作业】共 1 个")
    else:
        print(f"  【进行中的作业】共 {len(active)} 个")
    print()
    for i, a in enumerate(show, 1):
        print(f"    [{i}] ★ {a['text']}")
    print()

    if len(show) == 1:
        choice = 1
        print(f"  自动选择 [1]")
    else:
        try:
            raw = input(f"  请选择编号 [1-{len(show)}]: ").strip()
            choice = int(raw) if raw else 1
            if not (1 <= choice <= len(show)):
                choice = 1
        except (ValueError, EOFError):
            choice = 1

    chosen = show[choice - 1]
    ok(f"已选择: {chosen['text'][:60]}")
    return chosen["id"]


# ============ 步骤 4：列出未做题目 ============
async def list_todo(cdp, set_id):
    title("步骤 4/5  扫描题目")

    url = f"https://pintia.cn/problem-sets/{set_id}/exam/problems/type/7"
    await cdp.goto(url, wait=10)

    st = await core.get_state(cdp)
    problems = await core.list_problems(cdp)

    print()
    if st.get("progress"):
        p = st["progress"]
        print(f"  作业名  : {st.get('name', '?')}")
        print(f"  进度    : {p['done']} / {p['total']}")
    if st.get("deadline"):
        print(f"  剩余时间: {st['deadline']}")
    print()

    if not problems:
        bad("没读到题目列表")
        return None

    todo = [p for p in problems if p["status"] != "done"]
    done = [p for p in problems if p["status"] == "done"]

    print(f"  已做 {len(done)} 题，未做 {len(todo)} 题")
    print()
    if todo:
        print("  未做的题目：")
        for p in todo:
            print(f"    题 {p['num']:>2}   ({p['pid'][-4:]})")
    else:
        ok("全部做完了！")
        return {"setId": set_id, "todo": [], "problems": problems}

    print()
    return {"setId": set_id, "todo": todo, "problems": problems, "state": st}


# ============ 步骤 5：答题 ============
async def solve_all(cdp, cfg, task):
    title("步骤 5/5  自动答题")

    todo = task["todo"]
    if not todo:
        return

    set_id = task["setId"]
    base = f"https://pintia.cn/problem-sets/{set_id}/exam/problems/type/7"

    # 先测模型连接
    print()
    info("测试模型连接…")
    good, msg = ai.test_connection(cfg)
    if not good:
        bad(f"模型连接失败: {msg}")
        return
    ok(msg)

    auto_submit = cfg.get("答题设置", {}).get("自动提交", True)
    max_retry = cfg.get("答题设置", {}).get("样例不通过时最多重试次数", 2)
    force_submit = cfg.get("答题设置", {}).get("样例不通过是否仍提交", False)

    # 编译器设置
    compiler_key = cfg.get("答题设置", {}).get("编译器", "python3")
    compiler = core.resolve_compiler(compiler_key)
    lang = core.compiler_language(compiler_key)
    lang_name = ai.LANG_NAMES.get(lang, lang)

    print()
    info(f"编译器: {compiler}  (代码语言: {lang_name})")

    results = []

    for idx, p in enumerate(todo, 1):
        print()
        line()
        print(f"  [{idx}/{len(todo)}] 题 {p['num']}  ({p['pid'][-4:]})")
        line()

        # 切题
        info("切换题目…")
        if not await core.switch_problem(cdp, p["pid"]):
            bad("切题失败，跳过")
            results.append({"num": p["num"], "pid": p["pid"], "ok": False, "why": "切题失败"})
            continue

        # 读题面
        prob = await core.scrape_problem(cdp)
        if not prob or not prob.get("text"):
            bad("读不到题面，跳过")
            results.append({"num": p["num"], "pid": p["pid"], "ok": False, "why": "读不到题面"})
            continue
        info(f"题面 {len(prob['text'])} 字")

        # 生成答案（用配置的编译器语言）
        info(f"调用模型生成 {lang_name} 代码（最多 {max_retry + 1} 次）…")
        t0 = time.time()
        ans = ai.generate_answer(cfg, prob, max_retry=max_retry, language=lang)
        cost = time.time() - t0

        if not ans.get("code"):
            bad(f"生成失败: {ans.get('error', '未知')}")
            results.append({"num": p["num"], "pid": p["pid"], "ok": False,
                            "why": ans.get("error", "生成失败")})
            continue

        info(f"生成完成（{cost:.1f}s，{ans['attempts']} 次尝试，{len(ans['code'])} 字符）")

        if ans.get("skipped"):
            info(f"样例验证跳过：{ans.get('note', '本地无编译器')}")
        elif ans["verified"]:
            ok(f"样例验证通过（{ans['samples']} 个样例）")
        else:
            bad(f"样例验证未通过（{ans['samples']} 个样例）")
            for d in ans.get("details", []):
                if not d["pass"]:
                    print(f"       样例{d['i']}: {d['why'][:100]}")
            if not force_submit:
                info("按配置跳过提交（样例不通过不提交）")
                results.append({"num": p["num"], "pid": p["pid"], "ok": False,
                                "why": "样例未通过"})
                continue

        # 切换编译器
        c_ok, c_cur = await core.set_compiler(cdp, compiler)
        if c_ok:
            info(f"编译器: {c_cur}")
        else:
            bad(f"编译器切换失败: {c_cur}")
            info(f"继续用当前编译器提交（可能要出错）")

        # 写入
        w = await core.write_code(cdp, ans["code"])
        if not (isinstance(w, dict) and w.get("ok")):
            bad(f"写入失败: {(w or {}).get('reason', '')}")
            results.append({"num": p["num"], "pid": p["pid"], "ok": False, "why": "写入失败"})
            continue
        ok(f"已填入 {w['len']} 字符")

        if not auto_submit:
            info("按配置未自动提交")
            results.append({"num": p["num"], "pid": p["pid"], "ok": True,
                            "score": "-", "status": "已填入未提交"})
            continue

        # 提交
        s = await core.submit(cdp)
        if not (isinstance(s, dict) and s.get("ok")):
            bad(f"提交失败: {(s or {}).get('reason', '')}")
            results.append({"num": p["num"], "pid": p["pid"], "ok": False, "why": "提交失败"})
            continue

        info("已提交，等待评测…")
        r = await core.wait_result(cdp, max_wait=50)

        if r:
            status = r.get("状态", "?")
            score = r.get("分数", "?")
            if "正确" in status:
                ok(f"评测结果: {status}  {score} 分")
                results.append({"num": p["num"], "pid": p["pid"], "ok": True,
                                "score": score, "status": status})
            else:
                bad(f"评测结果: {status}  {score} 分")
                results.append({"num": p["num"], "pid": p["pid"], "ok": False,
                                "score": score, "status": status})
        else:
            info("评测超时，请到页面确认")
            results.append({"num": p["num"], "pid": p["pid"], "ok": True,
                            "score": "?", "status": "评测超时"})

    # ============ 汇总 ============
    print()
    title("完成汇总")

    passed = sum(1 for r in results if r.get("ok"))
    failed = len(results) - passed

    print()
    print(f"  {'题号':<6} {'结果':<12} {'分数':<6} {'状态'}")
    print(f"  {'-'*6} {'-'*12} {'-'*6} {'-'*20}")
    for r in results:
        mark = "✅" if r.get("ok") else "❌"
        print(f"  {r['num']:<6} {mark:<12} {str(r.get('score','-')):<6} {r.get('status', r.get('why',''))}")

    print()
    print(f"  通过 {passed} 题 / 失败 {failed} 题 / 共 {len(results)} 题")
    print()

    # 最后再确认一次总进度
    try:
        await cdp.goto(f"https://pintia.cn/problem-sets/{task['setId']}/exam/problems/type/7", wait=8)
        st = await core.get_state(cdp)
        if st.get("progress"):
            p = st["progress"]
            print(f"  当前作业进度: {p['done']} / {p['total']}")
            if p["done"] >= p["total"]:
                print()
                print("  🎉 全部完成！")
    except Exception:
        pass
    print()


# ============ 主流程 ============
async def main_async():
    title("PTA 一键答题")

    cfg = core.load_config()

    if not check_config(cfg):
        return 1

    cdp = core.CDP(cfg.get("浏览器", {}).get("调试端口", 9222))

    try:
        if not await connect_and_login(cdp, cfg):
            return 1

        set_id = await scan_assignments(cdp)
        if not set_id:
            return 1

        task = await list_todo(cdp, set_id)
        if not task:
            return 1

        if not task["todo"]:
            print("  没有需要做的题目。")
            return 0

        print()
        try:
            raw = input(f"  开始自动答题 {len(task['todo'])} 题？(Y/n) > ").strip().lower()
        except EOFError:
            raw = "y"
        if raw and raw != "y":
            print("  已取消")
            return 0

        await solve_all(cdp, cfg, task)
        return 0

    finally:
        await cdp.close()


def main():
    try:
        return asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n\n  已中断")
        return 130
    except Exception as e:
        print(f"\n  ❌ 出错: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
