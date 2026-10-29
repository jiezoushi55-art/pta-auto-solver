"""
模型调用 + 答案生成 + 本地样例验证
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error

# ============ 调用 OpenAI 兼容接口 ============
def call_model(cfg, prompt, system=None):
    """调用 OpenAI 兼容 API，返回文本"""
    base = cfg["baseUrl"].rstrip("/")
    key = cfg.get("apiKey", "").strip()
    model = cfg.get("model", "").strip()

    if not key:
        raise RuntimeError("config.json 里的 apiKey 还是空的，请先填写")
    if not model:
        raise RuntimeError("config.json 里的 model 是空的，请先填写")

    gen = cfg.get("生成参数", {})
    payload = {
        "model": model,
        "messages": [],
        "temperature": gen.get("temperature", 0.2),
        "max_tokens": gen.get("maxTokens", 4096),
    }
    if system:
        payload["messages"].append({"role": "system", "content": system})
    payload["messages"].append({"role": "user", "content": prompt})

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    timeout = gen.get("超时秒数", 180)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")[:500]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code}: {body}")
    except Exception as e:
        raise RuntimeError(f"请求失败: {e}")

    try:
        return resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise RuntimeError(f"返回格式异常: {json.dumps(resp, ensure_ascii=False)[:300]}")


def test_connection(cfg):
    """测试连接，返回 (ok, 消息)"""
    try:
        r = call_model(cfg, "只回答两个字：正常", system=None)
        return True, f"连接成功，模型回复: {r.strip()[:50]}"
    except Exception as e:
        return False, str(e)


# ============ 从题面解析样例 ============
def parse_samples(text):
    """从题面提取 (输入, 期望输出) 对"""
    samples = []
    norm = text.replace("\r", "")

    def clean(raw):
        lines = raw.split("\n")
        keep = []
        ui = re.compile(r"^\s*(复制内容|格式|全屏|收起|展开|测试用例|无提交记录)\s*$")
        for l in lines:
            t = l.strip()
            if not t:
                continue
            if ui.match(l):
                continue
            if re.search(r"感谢|鸣谢|补充测试数据|本题要求|输入格式|输出格式|代码长度限制", t):
                continue
            keep.append(l.lstrip())
        # 剥行号 1,2,3...
        start, expect = 0, 1
        while start < len(keep) and keep[start].strip().isdigit() and int(keep[start].strip()) == expect:
            start += 1
            expect += 1
        if start > 0 and start < len(keep):
            keep = keep[start:]
        elif start > 0 and start == len(keep):
            keep = [keep[-1]] if keep else []
        if not keep:
            return None
        return "\n".join(keep) + "\n"

    in_blocks = re.findall(
        r"\[\s*in\s*\](.*?)(?=\[\s*out\s*\]|输入样例|输出样例|代码长度限制|$)",
        norm, re.S | re.I)
    out_blocks = re.findall(
        r"\[\s*out\s*\](.*?)(?=\[\s*in\s*\]|输入样例|输出样例|代码长度限制|$)",
        norm, re.S | re.I)

    for i in range(min(len(in_blocks), len(out_blocks))):
        a, b = clean(in_blocks[i]), clean(out_blocks[i])
        if a is not None and b is not None:
            samples.append({"input": a, "expected": b})
    return samples


# 提示词统一在 pta_prompts.py 里维护
from pta_prompts import SYSTEM_PROMPTS, SYSTEM_PROMPT, DEFAULT_PROMPT, get_system_prompt, build_verify_feedback


def get_system_prompt(language):
    """按语言取提示词"""
    return SYSTEM_PROMPTS.get(language, DEFAULT_PROMPT)


# 向后兼容：默认 python
SYSTEM_PROMPT = SYSTEM_PROMPTS["python"]


LANG_NAMES = {
    "python": "Python3",
    "c": "C",
    "cpp": "C++",
    "java": "Java",
    "go": "Go",
    "javascript": "JavaScript (Node.js)",
    "csharp": "C#",
    "ruby": "Ruby",
    "pascal": "Pascal",
    "php": "PHP",
    "perl": "Perl",
    "lua": "Lua",
    "haskell": "Haskell",
    "ocaml": "OCaml",
    "awk": "AWK",
    "lisp": "Common Lisp",
    "bash": "Bash",
    "text": "纯文本",
}


def build_prompt(problem, samples, prev_fail=None, language="python"):
    lang_name = LANG_NAMES.get(language, language)
    parts = [f"【要求语言】{lang_name}", "", "【题目】", problem["text"].strip()[:6000]]

    if samples:
        parts.append("\n【样例（必须全部通过）】")
        for i, s in enumerate(samples, 1):
            parts.append(f"样例{i} 输入：")
            parts.append(s["input"].strip())
            parts.append(f"样例{i} 输出：")
            parts.append(s["expected"].strip())
            parts.append("")

    if prev_fail:
        # prev_fail["reason"] 现在已经是人类可读的反馈（含期望/实际对比）
        reason = str(prev_fail.get("reason") or "")
        if reason.startswith("【上次代码本地跑样例失败了"):
            # 已经是完整反馈，直接用
            parts.append("\n" + reason)
        else:
            parts.append("\n【上次的代码没通过，请修正】")
            parts.append(f"失败原因：{reason[:800]}")
            parts.append("上次的代码：")
            parts.append("```")
            parts.append(str(prev_fail.get("code") or "")[:1500])
            parts.append("```")

    parts.append(f"\n请用 {lang_name} 输出代码（记住：语法要简单，不要用高级特性，不要导入复杂的包）：")
    return "\n".join(parts)


def extract_code(reply, language="python"):
    """从模型回复里提取纯代码"""
    if not reply:
        return ""

    # 优先取 ``` 代码块
    m = re.search(r"```(?:[a-zA-Z+#]*)\s*\n(.*?)```", reply, re.S)
    if m:
        return m.group(1).strip()

    # 按语言找代码起始行
    starters = {
        "python": r"^(import |from |def |class |#|[a-zA-Z_]\w*\s*=)",
        "c": r"^(#include|#define|int |void |char |float |double |struct |//)",
        "cpp": r"^(#include|#define|using |int |void |class |//)",
        "java": r"^(import |package |public |class |//)",
        "go": r"^(package |import |func |//)",
        "javascript": r"^(const |let |var |function |//|process\.)",
        "csharp": r"^(using |namespace |class |public |//)",
    }
    pat = starters.get(language.lower(), r"^(import |from |def |class |#include|package |using |//)")

    lines = reply.split("\n")
    start = 0
    for i, l in enumerate(lines):
        t = l.strip()
        if not t:
            continue
        if re.match(pat, t):
            start = i
            break
    return "\n".join(lines[start:]).strip()


def norm(s):
    """标准化输出用于比对：去掉行尾空格和首尾空白"""
    return "\n".join(line.rstrip() for line in str(s or "").replace("\r", "").strip().split("\n"))


# ============ 多语言运行器 ============
def _run(cmd, stdin_text, timeout, cwd=None):
    """跑一个命令，返回 {ok, out, err}"""
    try:
        p = subprocess.run(
            cmd,
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
        )
        return {"ok": True, "out": p.stdout, "err": p.stderr}
    except subprocess.TimeoutExpired:
        return {"ok": False, "out": "", "err": "运行超时"}
    except FileNotFoundError as e:
        return {"ok": False, "out": "", "err": f"找不到命令: {e}"}
    except Exception as e:
        return {"ok": False, "out": "", "err": str(e)}


def _which(name):
    """检查命令是否存在"""
    from shutil import which
    return which(name)


def run_program(code, stdin_text, language="python", timeout=15):
    """
    按语言编译并运行，返回 {ok, out, err}
    本地没有对应编译器时返回 {ok: True, skipped: True}
    """
    lang = (language or "python").lower()
    tmpdir = tempfile.mkdtemp(prefix="pta_")

    try:
        # ---------- Python ----------
        if lang == "python":
            path = os.path.join(tmpdir, "main.py")
            with open(path, "w", encoding="utf-8") as f:
                f.write(code)
            return _run([sys.executable, path], stdin_text, timeout, cwd=tmpdir)

        # ---------- C ----------
        if lang == "c":
            if not _which("gcc"):
                return {"ok": True, "skipped": True, "err": "本地无 gcc"}
            src = os.path.join(tmpdir, "main.c")
            exe = os.path.join(tmpdir, "main.exe")
            with open(src, "w", encoding="utf-8") as f:
                f.write(code)
            r = _run(["gcc", src, "-o", exe, "-lm", "-O2"], "", 20, cwd=tmpdir)
            if not r["ok"] or not os.path.exists(exe):
                return {"ok": False, "out": "", "err": "编译失败: " + (r["err"] or "")[:300]}
            return _run([exe], stdin_text, timeout, cwd=tmpdir)

        # ---------- C++ ----------
        if lang == "cpp":
            if not _which("g++"):
                return {"ok": True, "skipped": True, "err": "本地无 g++"}
            src = os.path.join(tmpdir, "main.cpp")
            exe = os.path.join(tmpdir, "main.exe")
            with open(src, "w", encoding="utf-8") as f:
                f.write(code)
            r = _run(["g++", src, "-o", exe, "-std=c++17", "-O2"], "", 25, cwd=tmpdir)
            if not r["ok"] or not os.path.exists(exe):
                return {"ok": False, "out": "", "err": "编译失败: " + (r["err"] or "")[:300]}
            return _run([exe], stdin_text, timeout, cwd=tmpdir)

        # ---------- Java ----------
        if lang == "java":
            if not (_which("javac") and _which("java")):
                return {"ok": True, "skipped": True, "err": "本地无 JDK"}
            src = os.path.join(tmpdir, "Main.java")
            with open(src, "w", encoding="utf-8") as f:
                f.write(code)
            r = _run(["javac", "-encoding", "UTF-8", src], "", 30, cwd=tmpdir)
            if not r["ok"]:
                return {"ok": False, "out": "", "err": "编译失败: " + (r["err"] or "")[:300]}
            return _run(["java", "-Dfile.encoding=UTF-8", "-cp", tmpdir, "Main"],
                        stdin_text, timeout, cwd=tmpdir)

        # ---------- Go ----------
        if lang == "go":
            if not _which("go"):
                return {"ok": True, "skipped": True, "err": "本地无 go"}
            src = os.path.join(tmpdir, "main.go")
            with open(src, "w", encoding="utf-8") as f:
                f.write(code)
            # go run 需要 go.mod 或直接在目录里跑
            return _run(["go", "run", src], stdin_text, max(timeout, 30), cwd=tmpdir)

        # ---------- JavaScript ----------
        if lang == "javascript":
            if not _which("node"):
                return {"ok": True, "skipped": True, "err": "本地无 node"}
            src = os.path.join(tmpdir, "main.js")
            with open(src, "w", encoding="utf-8") as f:
                f.write(code)
            return _run(["node", src], stdin_text, timeout, cwd=tmpdir)

        # ---------- 其他语言：本地没有对应编译器 ----------
        return {"ok": True, "skipped": True, "err": f"本地没有 {lang} 的编译器"}

    finally:
        try:
            import shutil as _sh
            _sh.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


def verify_code(code, samples, language="python"):
    """跑所有样例，返回 {passed, details, skipped}"""
    if not samples:
        return {"passed": True, "details": [], "note": "无样例可验证", "skipped": False}

    details = []
    skipped = False

    for i, s in enumerate(samples, 1):
        r = run_program(code, s["input"], language)

        if r.get("skipped"):
            skipped = True
            break

        if not r["ok"]:
            details.append({"i": i, "pass": False, "why": f"运行错误: {r['err'][:150]}"})
            continue

        ok = norm(r["out"]) == norm(s["expected"])
        details.append({
            "i": i,
            "pass": ok,
            "why": "通过" if ok else f"期望[{norm(s['expected'])[:80]}] 实际[{norm(r['out'])[:80]}]",
        })

    if skipped:
        return {
            "passed": True,
            "details": [],
            "skipped": True,
            "note": "本地没有该语言的编译器，跳过验证（将直接提交）",
        }

    passed = all(d["pass"] for d in details)
    return {"passed": passed, "details": details, "skipped": False}


# ============ 生成答案（含重试）============
def generate_answer(cfg, problem, max_retry=2, language="python"):
    """
    生成答案：调模型 → 提取代码 → 本地验证 → 失败带错误重试
    language: python / c / cpp / java / go / javascript / ...
    返回 {code, verified, samples, attempts, error, language, skipped}
    """
    samples = parse_samples(problem["text"])
    prev_fail = None
    best = None
    sys_prompt = get_system_prompt(language)

    for attempt in range(1, max_retry + 2):
        prompt = build_prompt(problem, samples, prev_fail, language)
        try:
            reply = call_model(cfg, prompt, system=sys_prompt)
        except Exception as e:
            return {"code": "", "verified": False, "samples": len(samples),
                    "attempts": attempt, "error": str(e), "language": language}

        code = extract_code(reply, language)
        if not code:
            prev_fail = {"code": reply[:800], "reason": "模型没有返回有效代码"}
            continue

        v = verify_code(code, samples, language)

        if v["passed"]:
            return {
                "code": code,
                "verified": not v.get("skipped", False),
                "skipped": v.get("skipped", False),
                "samples": len(samples),
                "attempts": attempt,
                "error": None,
                "language": language,
                "details": v.get("details", []),
                "note": v.get("note", ""),
            }

        # 失败，带具体错误重试
        best = {"code": code, "verified": False, "skipped": False,
                "samples": len(samples), "attempts": attempt,
                "language": language, "details": v.get("details", [])}

        failed = [d for d in v.get("details", []) if not d.get("pass")]
        if failed:
            # 组装人类可读的失败反馈（包含期望/实际对比）
            prev_fail = {"code": code, "reason": build_verify_feedback(failed, code)}
        else:
            prev_fail = {"code": code, "reason": "代码运行出错，请检查语法"}

    return best or {"code": "", "verified": False, "samples": len(samples),
                    "attempts": max_retry + 1, "error": "生成失败", "language": language}
