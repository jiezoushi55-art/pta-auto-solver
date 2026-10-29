"""
PTA 核心操作层 —— 通过 CDP 操控浏览器完成读题、切题、写入、提交
"""

import asyncio
import json
import re
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).parent
CONFIG = ROOT / "config.json"


# ============ 配置 ============
def load_config():
    if not CONFIG.exists():
        raise RuntimeError(f"配置文件不存在: {CONFIG}")
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def save_config(cfg):
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# ============ PTA 多账号支持 ============
def get_accounts(cfg):
    """
    读取账号列表，统一成 [{'名字','账号','密码'}, ...]
    兼容旧格式（只有 pta.账号 / pta.密码）
    """
    pta = cfg.get("pta") or {}

    # 新格式：账号列表
    lst = pta.get("账号列表")
    if isinstance(lst, list) and lst:
        out = []
        for i, a in enumerate(lst):
            if not isinstance(a, dict):
                continue
            acc = (a.get("账号") or "").strip()
            if not acc:
                continue
            out.append({
                "名字": (a.get("名字") or acc).strip(),
                "账号": acc,
                "密码": (a.get("密码") or "").strip(),
            })
        if out:
            return out

    # 旧格式兼容
    acc = (pta.get("账号") or "").strip()
    pwd = (pta.get("密码") or "").strip()
    if acc:
        return [{"名字": acc, "账号": acc, "密码": pwd}]

    return []


def get_current_account(cfg):
    """取当前选中的账号，没有就返回第一个；全都没有返回 None"""
    accounts = get_accounts(cfg)
    if not accounts:
        return None

    pta = cfg.get("pta") or {}
    cur_name = (pta.get("当前账号") or "").strip()

    if cur_name:
        for a in accounts:
            if a["名字"] == cur_name or a["账号"] == cur_name:
                return a

    return accounts[0]


def set_accounts(cfg, accounts, current_name=None):
    """写回账号列表"""
    cfg.setdefault("pta", {})
    cfg["pta"]["账号列表"] = [
        {"名字": a["名字"], "账号": a["账号"], "密码": a["密码"]}
        for a in accounts
    ]
    if current_name:
        cfg["pta"]["当前账号"] = current_name

    # 清掉旧格式字段，避免混淆
    cfg["pta"].pop("账号", None)
    cfg["pta"].pop("密码", None)

    save_config(cfg)


def account_display_name(acc):
    """账号的简短显示名（打码）"""
    if not acc:
        return "（无）"
    name = acc.get("名字") or acc.get("账号") or ""
    acct = acc.get("账号") or ""
    if len(acct) > 6:
        masked = acct[:3] + "***" + acct[-3:]
    else:
        masked = acct
    return f"{name}（{masked}）" if name != acct else masked


# ============ CDP 客户端 ============
class CDP:
    def __init__(self, port=9222):
        self.port = port
        self.mid = 0
        self.pend = {}
        self.ws = None

    def _http(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
            return json.loads(r.read().decode())

    def list_pages(self):
        try:
            tabs = self._http("/json/list")
            return [t for t in tabs if t.get("type") == "page"]
        except Exception:
            return []

    def new_tab(self, url="about:blank"):
        """新建标签页（Chrome 新版要求 PUT）"""
        import urllib.parse
        quoted = urllib.parse.quote(url, safe="")
        # 先试 PUT
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/json/new?{quoted}",
                method="PUT",
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode())
        except Exception:
            pass
        # 再试 GET
        try:
            return self._http(f"/json/new?{quoted}")
        except Exception:
            return None

    async def _probe_ws_async(self, ws_url, timeout=6):
        """异步探测某个标签页的 CDP 是否响应"""
        try:
            ws = await asyncio.wait_for(
                websockets.connect(ws_url, max_size=2 ** 31), timeout=timeout)
            try:
                await ws.send(json.dumps({
                    "id": 1, "method": "Runtime.evaluate",
                    "params": {"expression": "1", "returnByValue": True}
                }))
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                m = json.loads(raw)
                return "result" in m
            finally:
                await ws.close()
        except Exception:
            return False

    async def connect(self, url_keyword="pintia.cn", force_new=False):
        """
        连接策略：
        1. 找一个 PTA 标签页，探测 CDP 是否响应
        2. 都不响应 → 新建标签页
        """
        pages = self.list_pages()

        candidates = []
        if not force_new:
            candidates = [p for p in pages if url_keyword in p.get("url", "")]
            # 倒序：优先试最新的（旧标签可能被别的工具占用）
            candidates = list(reversed(candidates))

        target = None
        for p in candidates:
            ws_url = p.get("webSocketDebuggerUrl", "")
            if not ws_url:
                continue
            if await self._probe_ws_async(ws_url):
                target = p
                break

        # 没有可用的就新建
        if not target:
            new = self.new_tab("https://pintia.cn/problem-sets/dashboard")
            if new and new.get("webSocketDebuggerUrl"):
                target = new
                await asyncio.sleep(3)
            else:
                raise RuntimeError(
                    "所有 PTA 标签页的调试通道都被占用，且无法新建标签页。\n"
                    "请关掉占用 9222 的其他工具（如 Playwright MCP），或重启调试浏览器。"
                )

        self.ws = await websockets.connect(target["webSocketDebuggerUrl"], max_size=2 ** 31)
        await asyncio.sleep(0.3)
        self._reader_task = asyncio.create_task(self._reader())
        await asyncio.sleep(0.2)
        await self.send("Page.enable", {})
        await self.send("Runtime.enable", {})
        try:
            await self.send("Page.addScriptToEvaluateOnNewDocument", {"source": """
                try { Object.defineProperty(window,'onbeforeunload',{configurable:true,get:()=>null,set:()=>{}}); } catch(e){}
            """})
        except Exception:
            pass
        return target

    async def _reader(self):
        try:
            async for raw in self.ws:
                m = json.loads(raw)
                i = m.get("id")
                if i and i in self.pend:
                    f = self.pend.pop(i)
                    if not f.done():
                        if "error" in m:
                            f.set_exception(RuntimeError(str(m["error"])[:200]))
                        else:
                            f.set_result(m.get("result", {}))
                    continue
                if m.get("method") == "Page.javascriptDialogOpening":
                    try:
                        self.mid += 1
                        await self.ws.send(json.dumps({
                            "id": self.mid, "method": "Page.handleJavaScriptDialog",
                            "params": {"accept": True}
                        }))
                    except Exception:
                        pass
        except Exception:
            pass

    async def send(self, method, params=None, tmo=60):
        self.mid += 1
        i = self.mid
        f = asyncio.get_event_loop().create_future()
        self.pend[i] = f
        await self.ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
        return await asyncio.wait_for(f, timeout=tmo)

    async def js(self, fn, arg=None, tmo=60):
        """执行 JS 函数表达式"""
        expr = f"({fn})({json.dumps(arg, ensure_ascii=False) if arg is not None else ''})"
        r = await self.send("Runtime.evaluate",
                            {"expression": expr, "returnByValue": True,
                             "awaitPromise": True, "userGesture": True}, tmo)
        res = (r.get("result", {}) if isinstance(r, dict) else {})
        if res.get("subtype") == "error":
            raise RuntimeError(res.get("description", "")[:200])
        return res.get("value")

    async def eval_raw(self, expr, tmo=60):
        r = await self.send("Runtime.evaluate",
                            {"expression": expr, "returnByValue": True, "awaitPromise": True}, tmo)
        res = (r.get("result", {}) if isinstance(r, dict) else {})
        return res.get("value")

    async def goto(self, url, wait=8):
        await self.send("Page.navigate", {"url": url}, tmo=60)
        await asyncio.sleep(wait)

    async def close(self):
        if self.ws:
            await self.ws.close()


# ============ PTA 页面操作 ============
JS_GET_STATE = """
() => {
  const url = location.href;
  const isLogin = url.includes('/auth/') || !!document.querySelector('input[type="password"]');
  const setId = (location.pathname.match(/problem-sets\\/(\\d+)/) || [])[1] || '';
  const pid = new URLSearchParams(location.search).get('problemSetProblemId') || '';
  // 顶部进度
  const body = document.body.innerText || '';
  let progress = null;
  const pm = body.match(/作答\\s*\\/\\s*题数[\\s\\S]{0,40}?(\\d+)\\s*\\/\\s*(\\d+)/);
  if (pm) progress = { done: +pm[1], total: +pm[2] };
  // 剩余时间
  let deadline = null;
  const dm = body.match(/(\\d+)\\s*(天|小时|分钟)\\s*后关闭/);
  if (dm) deadline = dm[0];
  // 作业名
  const h = document.querySelector('h1, [class*="problemSetName"], [class*="title"]');
  const name = h ? (h.innerText||'').trim().split('\\n')[0] : document.title;
  return { url, isLogin, setId, pid, progress, deadline, name };
}
"""

JS_LIST_PROBLEMS = """
() => {
  const out = [];

  // 优先用表格（含标号/标题/分数/通过率/状态图标）
  const tb = document.querySelector('table');
  if (tb) {
    const heads = Array.from(tb.querySelectorAll('thead th')).map(h => (h.innerText||'').trim());
    const rows = Array.from(tb.querySelectorAll('tbody tr'));
    rows.forEach(tr => {
      const tds = Array.from(tr.querySelectorAll('td'));
      if (tds.length < 3) return;

      // 第一列是状态图标
      const iconTd = tds[0];
      const svgUse = iconTd.querySelector('use');
      const iconId = svgUse ? (svgUse.getAttribute('xlink:href') || svgUse.getAttribute('href') || '') : '';
      const iconCls = (iconTd.querySelector('.pc-icon') || {}).className || '';

      // 判断状态
      let status = 'unknown';
      if (/check/i.test(iconId)) status = 'done';
      else if (/close|fail|error|wrong/i.test(iconId)) status = 'failed';
      else if (!iconId && !iconCls.includes('pc-color')) status = 'todo';

      // 标号、标题、分数
      const num = (tds[1].innerText || '').trim();
      const titleTd = tds[2];
      const title = (titleTd.innerText || '').trim();
      // pid 在 div id 上
      const divWithId = titleTd.querySelector('div[id]');
      const pid = divWithId ? divWithId.id : '';
      const score = (tds[3] ? (tds[3].innerText || '').trim() : '');
      const rate = (tds[4] ? (tds[4].innerText || '').trim().replace(/\\n/g, ' ') : '');

      if (!pid) return;

      out.push({
        pid,
        num: num || title,
        title: title,
        score: score,
        rate: rate,
        status: status,
        iconId: iconId
      });
    });
  }

  // 表格拿不到就退回左栏链接
  if (!out.length) {
    const seen = new Set();
    document.querySelectorAll('a[href*="problemSetProblemId="]').forEach(a => {
      const m = (a.getAttribute('href')||'').match(/problemSetProblemId=(\\d+)/);
      if (!m || seen.has(m[1])) return;
      const r = a.getBoundingClientRect();
      if (r.width < 20 || r.height < 20) return;
      seen.add(m[1]);
      out.push({ pid: m[1], num: (a.innerText||'').trim(), title: '', score: '', rate: '', status: 'unknown' });
    });
  }

  out.sort((a,b) => (parseInt(a.num)||99) - (parseInt(b.num)||99));
  return out;
}
"""

JS_SWITCH = """
(pid) => {
  const a = Array.from(document.querySelectorAll('a[href*="problemSetProblemId="]'))
    .find(x => (x.getAttribute('href')||'').includes(pid));
  if (!a) return false;
  a.click();
  return true;
}
"""

JS_READY = """
(pid) => {
  const cur = new URLSearchParams(location.search).get('problemSetProblemId') || '';
  const nodes = Array.from(document.querySelectorAll('.cm-content'));
  let view = null;
  for (const el of nodes) {
    if (el.getAttribute('contenteditable') !== 'true') continue;
    const v = el.cmTile && el.cmTile.view;
    if (v && !v.state.readOnly) { view = v; break; }
  }
  return { pid: cur, match: cur === pid, ready: !!view, docLen: view ? view.state.doc.length : -1 };
}
"""

JS_SCRAPE = """
() => {
  const cur = new URLSearchParams(location.search).get('problemSetProblemId') || '';
  // 题面：找含"输入格式/输出格式/输入样例"的容器
  const anchors = Array.from(document.querySelectorAll('div,section,article')).filter(el => {
    const t = el.innerText || '';
    return t.length > 80 && t.length < 30000 &&
      (t.includes('输入格式') || t.includes('输出格式') || t.includes('输入样例'));
  });
  let text = '';
  if (anchors.length) {
    anchors.sort((a,b) => (a.innerText||'').length - (b.innerText||'').length);
    text = anchors[0].innerText || '';
  } else {
    const m = document.querySelector('main') || document.body;
    text = (m.innerText||'').slice(0, 15000);
  }
  const h = document.querySelector('h1,h2,h3,[class*="problemTitle"]');
  const title = h ? (h.innerText||'').trim().split('\\n')[0] : document.title;
  return { pid: cur, title, text: text.trim() };
}
"""

JS_WRITE = """
(code) => {
  const nodes = Array.from(document.querySelectorAll('.cm-content'));
  let view = null;
  for (const el of nodes) {
    if (el.getAttribute('contenteditable') !== 'true') continue;
    const v = el.cmTile && el.cmTile.view;
    if (!v || v.state.readOnly) continue;
    view = v; break;
  }
  if (!view) return { ok:false, reason:'没有可编辑的编辑器' };
  try {
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: code },
      selection: { anchor: code.length },
      scrollIntoView: true
    });
  } catch (e) { return { ok:false, reason: e.message }; }
  const after = view.state.doc.toString();
  return { ok: after === code, len: after.length };
}
"""

JS_SUBMIT = """
() => {
  const btns = Array.from(document.querySelectorAll('button'));
  const b = btns.find(x => (x.innerText||'').trim().includes('提交本题作答'));
  if (!b) return { ok:false, reason:'找不到提交按钮' };
  if (b.disabled) return { ok:false, reason:'按钮禁用' };
  b.click();
  return { ok:true };
}
"""

JS_RESULT = """
() => {
  // PTA 提交后，评测结果显示在页面里（不是表格）
  // 优先级：
  //   1. 页面里出现 "答案正确/答案错误/..." 等状态词
  //   2. 表格（提交列表页才有）

  const STATUS_WORDS = [
    '答案正确','答案错误','部分正确','编译错误','运行超时','内存超限',
    '格式错误','返回非零','非零返回','段错误','浮点错误','多种错误',
    '正在评测','等待评测','评测中','排队中'
  ];

  // ---- 1) 先找状态词 ----
  let statusText = '';
  let statusEl = null;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const t = (node.textContent || '').trim();
    if (!t || t.length > 20) continue;
    for (const w of STATUS_WORDS) {
      if (t === w || t.includes(w)) {
        // 排除图例里的说明文字
        const el = node.parentElement;
        if (!el) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 10 || r.height < 8) continue;
        // 排除 x 在左侧栏题号区（x<100）
        if (r.x < 100) continue;
        statusText = t;
        statusEl = el;
        break;
      }
    }
    if (statusText) break;
  }

  // ---- 2) 找分数 ----
  let score = '';
  const body = document.body.innerText || '';
  // 常见形式： "10/10" "15 分" "分数 10"
  const m1 = body.match(/(\\d+)\\s*\\/\\s*(\\d+)\\s*(?:分)?/);
  // 只取状态词附近 300 字内的分数，避免误抓通过率
  if (statusEl) {
    let p = statusEl.parentElement;
    for (let i = 0; i < 5 && p; i++) {
      const t = (p.innerText || '');
      const sm = t.match(/(\\d+)\\s*\\/\\s*(\\d+)/);
      if (sm && sm[1].length <= 3) { score = sm[1] + '/' + sm[2]; break; }
      p = p.parentElement;
    }
  }

  // ---- 3) 表格兜底（提交列表页）----
  const tb = document.querySelector('table');
  let tableRow = null;
  if (tb) {
    const row = tb.querySelector('tbody tr');
    if (row) {
      const heads = Array.from(tb.querySelectorAll('thead th')).map(h => (h.innerText||'').trim());
      const cells = Array.from(row.querySelectorAll('td')).map(c => (c.innerText||'').trim());
      tableRow = {};
      heads.forEach((h, i) => { tableRow[h] = cells[i] || ''; });
    }
  }

  // 组装结果
  if (statusText) {
    return {
      '状态': statusText,
      '分数': score || (tableRow ? tableRow['分数'] : '') || '',
      '来源': 'page'
    };
  }
  if (tableRow && tableRow['状态']) {
    return tableRow;
  }
  return null;
}
"""

JS_COMPILER = """
() => {
  const i = document.querySelector('[id^="react-select-"][role="combobox"]');
  if (!i) return { ok:false };
  return { ok:true, id:i.id, current: (document.querySelector('.select__control')||{}).innerText || '' };
}
"""

# ============ 编译器支持 ============
# PTA 支持的编译器（实测 22 个）
COMPILERS = [
    "C (gcc)", "C++ (g++)", "C (clang)", "C++ (clang++)",
    "Java (javac)", "Python (python2)", "Python (python3)",
    "Ruby (ruby)", "Bash (bash)", "Plaintext (cat)",
    "Common Lisp (sbcl)", "Pascal (fpc)", "Go (go)", "Haskell (ghc)",
    "Lua (lua)", "Lua (luajit)", "C# (dotnet)", "JavaScript (node)",
    "OCaml (ocamlc)", "PHP (php)", "Perl (perl)", "AWK (awk)",
]

# 简称 → 完整名
COMPILER_ALIASES = {
    "python": "Python (python3)",
    "python3": "Python (python3)",
    "py": "Python (python3)",
    "py3": "Python (python3)",
    "python2": "Python (python2)",
    "py2": "Python (python2)",
    "c": "C (gcc)",
    "gcc": "C (gcc)",
    "c++": "C++ (g++)",
    "cpp": "C++ (g++)",
    "g++": "C++ (g++)",
    "cxx": "C++ (g++)",
    "java": "Java (javac)",
    "javac": "Java (javac)",
    "go": "Go (go)",
    "golang": "Go (go)",
    "js": "JavaScript (node)",
    "javascript": "JavaScript (node)",
    "node": "JavaScript (node)",
    "nodejs": "JavaScript (node)",
    "ruby": "Ruby (ruby)",
    "bash": "Bash (bash)",
    "sh": "Bash (bash)",
    "pascal": "Pascal (fpc)",
    "fpc": "Pascal (fpc)",
    "c#": "C# (dotnet)",
    "csharp": "C# (dotnet)",
    "dotnet": "C# (dotnet)",
    "php": "PHP (php)",
    "perl": "Perl (perl)",
    "lua": "Lua (lua)",
    "luajit": "Lua (luajit)",
    "haskell": "Haskell (ghc)",
    "ghc": "Haskell (ghc)",
    "ocaml": "OCaml (ocamlc)",
    "awk": "AWK (awk)",
    "lisp": "Common Lisp (sbcl)",
    "sbcl": "Common Lisp (sbcl)",
    "cat": "Plaintext (cat)",
    "plaintext": "Plaintext (cat)",
    "text": "Plaintext (cat)",
}


def resolve_compiler(name):
    """
    把用户写的编译器名解析成 PTA 的完整名
    支持：完整名精确匹配 > 简称 > 模糊包含
    """
    if not name:
        return "Python (python3)"

    s = str(name).strip()

    # 1) 完整名精确匹配（忽略大小写）
    for c in COMPILERS:
        if c.lower() == s.lower():
            return c

    # 2) 简称
    key = s.lower()
    if key in COMPILER_ALIASES:
        return COMPILER_ALIASES[key]

    # 3) 模糊包含（比如 "python3" 匹配 "Python (python3)"）
    for c in COMPILERS:
        if key in c.lower():
            return c

    # 4) 兜底
    return "Python (python3)"


def compiler_language(name):
    """
    从编译器名判断语言，用于给 AI 提示词
    返回 'python' / 'c' / 'cpp' / 'java' / 'go' / 'javascript' / ...
    """
    full = resolve_compiler(name).lower()
    if "python" in full:
        return "python"
    if "c++" in full or "g++" in full or "clang++" in full:
        return "cpp"
    if "c (" in full or "gcc" in full:
        return "c"
    if "java" in full:
        return "java"
    if "go (" in full:
        return "go"
    if "javascript" in full or "node" in full:
        return "javascript"
    if "c#" in full or "dotnet" in full:
        return "csharp"
    if "ruby" in full:
        return "ruby"
    if "pascal" in full:
        return "pascal"
    if "php" in full:
        return "php"
    if "perl" in full:
        return "perl"
    if "lua" in full:
        return "lua"
    if "haskell" in full:
        return "haskell"
    if "ocaml" in full:
        return "ocaml"
    if "awk" in full:
        return "awk"
    if "lisp" in full:
        return "lisp"
    if "bash" in full:
        return "bash"
    return "text"


async def get_state(cdp):
    return await cdp.js(JS_GET_STATE)


async def list_problems(cdp):
    return await cdp.js(JS_LIST_PROBLEMS) or []


async def switch_problem(cdp, pid, timeout=30):
    """切到指定题目并等编辑器就绪"""
    for _ in range(3):
        try:
            ok = await cdp.js(JS_SWITCH, pid, tmo=20)
        except Exception:
            ok = False
        if ok:
            end = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < end:
                try:
                    st = await cdp.js(JS_READY, pid, tmo=10)
                    if st and st.get("match") and st.get("ready"):
                        return True
                except Exception:
                    pass
                await asyncio.sleep(1)
        await asyncio.sleep(1.5)
    return False


async def scrape_problem(cdp):
    return await cdp.js(JS_SCRAPE)


# 纯 JS 切换编译器 —— 直接调 react-select 内部 selectOption()
# 完全不模拟键盘/鼠标，不会让标签页跳前台
JS_SET_COMPILER = """
(wantLabel) => {
  const root = document.querySelector('[class*="select_"][class*="css-"]');
  if (!root) return JSON.stringify({ ok:false, reason:\'找不到 select 容器\' });

  const fk = Object.keys(root).find(k => k.startsWith(\'__reactFiber\'));
  if (!fk) return JSON.stringify({ ok:false, reason:\'找不到 fiber\' });

  let fiber = root[fk];
  let d = 0, inst = null;
  while (fiber && d < 40) {
    const sn = fiber.stateNode;
    if (sn && sn.props && sn.state && typeof sn.selectOption === \'function\') { inst = sn; break; }
    fiber = fiber.return; d++;
  }
  if (!inst) return JSON.stringify({ ok:false, reason:\'找不到 Select 实例\', depth:d });

  const opts = inst.props.options || [];

  // label 是 React 元素，取不到文本 —— 直接用 value 匹配
  // PTA 的 value 命名规则（实测 37 项）：
  // GCC GXX CLANG CLANGXX JAVAC PYTHON2 PYTHON3 RUBY BASH CAT CLISP FPC GO GHC
  // LUA LUAJIT MCS NODE OCAMLC PHP PERL AWK DMD RKT VALAC VBNC KOTLIN SWIFT
  // OBJC FORTRAN OCTAVE RLANG ASM RUST SCALA PYPY3
  function toValue(s) {
    const v = String(s).toLowerCase();
    if (v.includes(\'python3\')) return \'PYTHON3\';
    if (v.includes(\'python2\')) return \'PYTHON2\';
    if (v.includes(\'pypy3\'))   return \'PYPY3\';
    if (v.includes(\'c++\') || v.includes(\'g++\') || v.includes(\'gxx\')) return \'GXX\';
    if (v.includes(\'clang++\')) return \'CLANGXX\';
    if (v.includes(\'clang\'))   return \'CLANG\';
    if (v.includes(\'java\'))    return \'JAVAC\';
    if (v.includes(\'go (\') || v === \'go\') return \'GO\';
    if (v.includes(\'node\') || v.includes(\'javascript\')) return \'NODE\';
    if (v.includes(\'ruby\'))    return \'RUBY\';
    if (v.includes(\'bash\'))    return \'BASH\';
    if (v.includes(\'c#\') || v.includes(\'dotnet\')) return \'MCS\';
    if (v.includes(\'php\'))     return \'PHP\';
    if (v.includes(\'perl\'))    return \'PERL\';
    if (v.includes(\'lua\'))     return \'LUA\';
    if (v.includes(\'haskell\')) return \'GHC\';
    if (v.includes(\'pascal\'))  return \'FPC\';
    if (v.includes(\'ocaml\'))   return \'OCAMLC\';
    if (v.includes(\'awk\'))     return \'AWK\';
    if (v.includes(\'lisp\'))    return \'CLISP\';
    if (v.includes(\'plaintext\') || v.includes(\'cat\')) return \'CAT\';
    if (v.includes(\'rust\'))    return \'RUST\';
    if (v.includes(\'kotlin\'))  return \'KOTLIN\';
    if (v.includes(\'swift\'))   return \'SWIFT\';
    if (v.includes(\'scala\'))   return \'SCALA\';
    if (v.includes(\'c (\') || v === \'c\' || v.includes(\'gcc\')) return \'GCC\';
    return \'\'.toUpperCase();
  }

  const want = toValue(wantLabel);
  let target = null;
  for (const o of opts) {
    if (String(o.value) === want) { target = o; break; }
  }

  if (!target) {
    return JSON.stringify({
      ok:false, reason:\'找不到该编译器\',
      want, wantLabel,
      availableValues: opts.map(o => String(o.value)).filter(Boolean)
    });
  }

  const curArr = inst.state.selectValue || [];
  const beforeVal = curArr.length ? String(curArr[0].value) : \'\';

  try {
    inst.selectOption(target);
  } catch (e) {
    return JSON.stringify({ ok:false, reason:\'selectOption 异常: \' + e.message });
  }

  return JSON.stringify({
    ok: true,
    picked: String(target.value),
    before: beforeVal,
    depth: d
  });
}
"""


async def set_compiler_bg(cdp, target):
    """
    后台切换编译器（纯 JS，不模拟输入，不抢前台）
    返回 (ok, 当前编译器名)
    """
    full_name = resolve_compiler(target)

    # 已经对了就不动
    try:
        cur_info = await cdp.js(JS_COMPILER)
        if isinstance(cur_info, dict) and cur_info.get("ok"):
            cur = str(cur_info.get("current") or "").strip()
            if cur.lower() == full_name.lower():
                return True, cur
    except Exception:
        pass

    import json as _json

    expr = (
        "(() => {\n"
        "const fn = " + JS_SET_COMPILER.strip() + ";\n"
        "return fn(" + _json.dumps(full_name, ensure_ascii=False) + ");\n"
        "})()"
    )

    result_raw = await cdp.eval_raw(expr, tmo=30)

    if not result_raw:
        return False, "执行失败（无返回）"

    try:
        r = _json.loads(result_raw) if isinstance(result_raw, str) else result_raw
    except Exception:
        return False, f"返回值解析失败: {str(result_raw)[:100]}"

    if not r.get("ok"):
        return False, f"切换失败: {r.get('reason')}"

    # 等 React 更新完，读回实际值
    await asyncio.sleep(0.8)
    cur = await cdp.js("() => {const x=document.querySelector('.select__control'); return x?(x.innerText||'').trim():'?';}")
    cur_s = str(cur).strip()
    return cur_s.lower() == full_name.lower(), cur_s


async def _open_select_menu(cdp, rid, retries=3):
    """
    打开 react-select 的下拉菜单
    返回 True/False
    """
    for attempt in range(retries):
        # 确保标签页可见
        try:
            await cdp.send("Page.bringToFront", {})
        except Exception:
            pass
        await asyncio.sleep(0.5)

        # 先点一下页面空白，确保页面有焦点
        if attempt == 0:
            try:
                await cdp.js("() => { window.focus(); if(document.body) document.body.click(); return true; }")
            except Exception:
                pass
            await asyncio.sleep(0.3)

        # 聚焦 input 再按 ArrowDown
        await cdp.js(f"() => {{const i=document.getElementById('{rid}'); if(i) {{ i.focus(); }} return !!i;}}")
        await asyncio.sleep(0.4)
        await cdp.send("Input.dispatchKeyEvent", {"type": "rawKeyDown", "windowsVirtualKeyCode": 40, "code": "ArrowDown", "key": "ArrowDown"})
        await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "windowsVirtualKeyCode": 40, "code": "ArrowDown", "key": "ArrowDown"})
        await asyncio.sleep(1.0)

        opened = await cdp.js("() => !!document.querySelector('.select__menu')")
        if opened:
            return True

        # 没打开，用鼠标点击试试
        box = await cdp.js("""
        () => {
          const c = document.querySelector('.select__control');
          if (!c) return null;
          const r = c.getBoundingClientRect();
          return { x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2) };
        }
        """)
        if box:
            await cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": box["x"], "y": box["y"], "button": "left", "clickCount": 1})
            await cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": box["x"], "y": box["y"], "button": "left", "clickCount": 1})
            await asyncio.sleep(1.0)
            opened = await cdp.js("() => !!document.querySelector('.select__menu')")
            if opened:
                return True

        await asyncio.sleep(0.5)

    return False


async def set_compiler(cdp, target):
    """
    切换编译器（优先后台方式，不抢前台）
    target 可以是完整名或简称
    返回 (ok, 当前编译器名)
    """
    # 先试纯 JS 后台方式
    try:
        ok, cur = await set_compiler_bg(cdp, target)
        if ok:
            return True, cur
    except Exception:
        pass

    # 后台方式失败，回退到模拟输入（会抢前台）
    full_name = resolve_compiler(target)

    try:
        cur_info = await cdp.js(JS_COMPILER)
        if isinstance(cur_info, dict) and cur_info.get("ok"):
            cur = str(cur_info.get("current") or "").strip()
            if cur.lower() == full_name.lower():
                return True, cur
    except Exception:
        pass

    info = await cdp.js(JS_COMPILER)
    if not (isinstance(info, dict) and info.get("ok")):
        return False, "无下拉框"

    rid = info["id"]
    if not await _open_select_menu(cdp, rid):
        cur = await cdp.js("() => {const x=document.querySelector(\'.select__control\'); return x?(x.innerText||\'\').trim():\'?\';}")
        return False, f"菜单打不开，当前: {cur}"

    import json as _json
    picked = await cdp.js(f"""
    () => {{
      const want = {_json.dumps(full_name, ensure_ascii=False)};
      const menu = document.querySelector(\'.select__menu\');
      if (!menu) return {{ ok:false, reason:\'菜单没打开\' }};
      const opts = Array.from(menu.querySelectorAll(\'.select__option\'));
      let idx = opts.findIndex(o => (o.innerText||\'\').trim() === want);
      if (idx < 0) idx = opts.findIndex(o => (o.innerText||\'\').trim().toLowerCase() === want.toLowerCase());
      if (idx < 0) return {{ ok:false, reason:\'找不到选项\' }};
      const r = opts[idx].getBoundingClientRect();
      return {{ ok:true, x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2) }};
    }}
    """)

    if not (isinstance(picked, dict) and picked.get("ok")):
        await cdp.send("Input.dispatchKeyEvent", {"type": "rawKeyDown", "windowsVirtualKeyCode": 27, "code": "Escape", "key": "Escape"})
        cur = await cdp.js("() => {const x=document.querySelector(\'.select__control\'); return x?(x.innerText||\'\').trim():\'?\';}")
        return False, f"切换失败，当前: {cur}"

    await cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": picked["x"], "y": picked["y"], "button": "left", "clickCount": 1})
    await cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": picked["x"], "y": picked["y"], "button": "left", "clickCount": 1})
    await asyncio.sleep(1.2)

    cur = await cdp.js("() => {const x=document.querySelector(\'.select__control\'); return x?(x.innerText||\'\').trim():\'?\';}")
    cur_s = str(cur).strip()
    return cur_s.lower() == full_name.lower(), cur_s


async def set_python3(cdp):
    """兼容旧调用：切到 python3"""
    return await set_compiler(cdp, "python3")


async def write_code(cdp, code):
    return await cdp.js(JS_WRITE, code, tmo=60)


async def submit(cdp):
    return await cdp.js(JS_SUBMIT, tmo=30)


async def read_result(cdp):
    return await cdp.js(JS_RESULT, tmo=15)


async def wait_result(cdp, max_wait=50):
    """等评测结果"""
    end = asyncio.get_event_loop().time() + max_wait
    while asyncio.get_event_loop().time() < end:
        await asyncio.sleep(2)
        try:
            r = await read_result(cdp)
            if isinstance(r, dict):
                st = r.get("状态", "")
                if st and st not in ("正在评测", "等待评测", "?"):
                    return r
        except Exception:
            pass
    return None
