"""
重跑指定作业的未通过题目
用法: python pta_rerun.py <setId>
"""
import asyncio
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pta_core as core
import pta_ai as ai


def line(ch="─", n=62):
    print(ch * n)


async def main():
    cfg = core.load_config()
    cdp = core.CDP(cfg.get("浏览器", {}).get("调试端口", 9222))

    # 作业 ID
    set_id = sys.argv[1] if len(sys.argv) > 1 else "2115699393692123136"
    base = f"https://pintia.cn/problem-sets/{set_id}/exam/problems/type/7"

    await cdp.connect()

    # 编译器 / 语言
    compiler_key = cfg.get("答题设置", {}).get("编译器", "python3")
    compiler = core.resolve_compiler(compiler_key)
    lang = core.compiler_language(compiler_key)
    lang_name = ai.LANG_NAMES.get(lang, lang)

    auto_submit = cfg.get("答题设置", {}).get("自动提交", True)
    max_retry = cfg.get("答题设置", {}).get("样例不通过时最多重试次数", 2)
    force_submit = cfg.get("答题设置", {}).get("样例不通过是否仍提交", False)

    print()
    line("═")
    print("  重跑未通过的题目")
    line("═")
    print(f"  作业: {set_id}")
    print(f"  编译器: {compiler}")

    # 回列表页读状态
    await cdp.goto(base, wait=10)
    probs = await core.list_problems(cdp)

    todo = [p for p in probs if p["status"] != "done"]
    if not todo:
        print("\n  ✅ 所有题目都已通过，无需重跑")
        await cdp.close()
        return 0

    print(f"\n  发现 {len(todo)} 题需要重做：")
    for p in todo:
        print(f"    {p['num']:<8} {p.get('title','')[:36]}  ({p['status']})")

    print()
    try:
        raw = input(f"  开始重跑？(Y/n) > ").strip().lower()
    except EOFError:
        raw = "y"
    if raw and raw != "y":
        print("  已取消")
        await cdp.close()
        return 0

    results = []

    for idx, p in enumerate(todo, 1):
        print()
        line()
        print(f"  [{idx}/{len(todo)}] {p['num']}  {p.get('title','')[:40]}")
        line()

        # 切题（点链接）
        await cdp.js(f"""() => {{
          const a = Array.from(document.querySelectorAll('a[href*="problemSetProblemId="]'))
            .find(x => (x.getAttribute('href')||'').includes('{p['pid']}'));
          if (a) a.click(); return !!a;
        }}""")
        await asyncio.sleep(7)

        # 读题面
        prob = await core.scrape_problem(cdp)
        if not prob or not prob.get("text"):
            print("  ❌ 读不到题面")
            results.append((p["num"], False, "读不到题面"))
            continue
        print(f"  题面 {len(prob['text'])} 字")

        samples = ai.parse_samples(prob["text"])
        print(f"  样例 {len(samples)} 个")

        # 生成
        print(f"  生成 {lang_name} 代码…")
        t0 = time.time()
        ans = ai.generate_answer(cfg, prob, max_retry=max_retry, language=lang)
        cost = time.time() - t0

        if not ans.get("code"):
            print(f"  ❌ 生成失败: {ans.get('error')}")
            results.append((p["num"], False, "生成失败"))
            continue

        print(f"  生成完成（{cost:.1f}s，{ans['attempts']} 次）")

        if ans.get("skipped"):
            print(f"  ⚠ 样例验证跳过：{ans.get('note','')}")
        elif ans["verified"]:
            print(f"  ✅ 样例验证通过（{ans['samples']} 个）")
        else:
            print(f"  ❌ 样例未通过")
            for d in ans.get("details", []):
                if not d["pass"]:
                    print(f"     样例{d['i']}: {d['why'][:110]}")
            if not force_submit:
                print("  按配置跳过提交")
                results.append((p["num"], False, "样例未通过"))
                continue

        # 后台切编译器
        c_ok, c_cur = await core.set_compiler_bg(cdp, compiler)
        print(f"  编译器: {c_cur}")

        # 写入
        w = await core.write_code(cdp, ans["code"])
        if not (isinstance(w, dict) and w.get("ok")):
            print(f"  ❌ 写入失败")
            results.append((p["num"], False, "写入失败"))
            continue
        print(f"  已填入 {w['len']} 字符")

        if not auto_submit:
            results.append((p["num"], True, "已填入未提交"))
            continue

        # 提交
        s = await core.submit(cdp)
        if not (isinstance(s, dict) and s.get("ok")):
            print(f"  ❌ 提交失败")
            results.append((p["num"], False, "提交失败"))
            continue

        print(f"  已提交，等评测…")
        r = await core.wait_result(cdp, max_wait=40)

        if r:
            status = r.get("状态", "?")
            score = r.get("分数", "")
            good = "正确" in status and "错误" not in status
            print(f"  {'✅' if good else '❌'} {status}  {score}")
            results.append((p["num"], good, status))
        else:
            print(f"  ⏱ 评测超时")
            results.append((p["num"], False, "评测超时"))

    # 汇总
    print()
    line("═")
    print("  重跑结果")
    line("═")
    ok = sum(1 for _, g, _ in results if g)
    for num, good, msg in results:
        print(f"  {'✅' if good else '❌'} {num:<8} {msg}")
    print()
    print(f"  通过 {ok} / {len(results)}")
    line("═")
    print()

    await cdp.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
