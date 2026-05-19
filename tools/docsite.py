#!/usr/bin/env python3
"""Build a self-contained HTML documentation site from generated Markdown docs."""
from __future__ import annotations

import argparse
import html
import importlib.util
import json
import re
import shutil
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple


TOOLS_DIR = Path(__file__).resolve().parent
DEFAULT_VENDOR_DIR = TOOLS_DIR / "html_vendor"


REQUIRED_VENDOR_FILES = [
    "mermaid/mermaid.min.js",
    "marked/marked.umd.js",
    "mathjax/tex-mml-svg.js",
    "mathjax-newcm-font/svg.js",
    "mathjax-newcm-font/svg/dynamic/calligraphic.js",
    "highlight/highlight.min.js",
    "highlight/styles/github.min.css",
]

VENDOR_COPY_ITEMS = [
    "mermaid/mermaid.min.js",
    "marked/marked.umd.js",
    "highlight/highlight.min.js",
    "highlight/styles/github.min.css",
    "mathjax/tex-mml-svg.js",
    "mathjax-newcm-font/svg.js",
    "mathjax-newcm-font/svg/dynamic",
]


DOC_ORDER = [
    "README.md",
    "ARCHITECTURE.md",
    "ALGORITHMS.md",
    "WEBUI_RUNTIME.md",
    "MASK_LITHOGRAPHY.md",
    "AGENT_KNOWLEDGE.md",
    "DEVELOPER_GUIDE.md",
]


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def slugify(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", value.strip()).strip("-").lower()
    return slug or "section"


def page_slug(path: Path) -> str:
    return slugify(path.stem)


def html_title(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip(" `") or fallback
    return fallback


def extract_headings(markdown: str, *, max_level: int = 3) -> List[Tuple[int, str, str]]:
    headings: List[Tuple[int, str, str]] = []
    in_fence = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,%d})\s+(.+?)\s*$" % max_level, line)
        if not match:
            continue
        level = len(match.group(1))
        text = re.sub(r"`([^`]+)`", r"\1", match.group(2)).strip()
        headings.append((level, text, slugify(text)))
    return headings


def docs_sorted(docs_dir: Path) -> List[Path]:
    found = {p.name: p for p in docs_dir.glob("*.md")}
    ordered = [found[name] for name in DOC_ORDER if name in found]
    ordered.extend(sorted(p for name, p in found.items() if name not in DOC_ORDER))
    return ordered


def sanitize_mermaid_label(label: str) -> str:
    """Keep Mermaid 11 labels parseable while preserving the intended meaning."""
    value = html.unescape(label)
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    value = value.replace("`", "").replace('"', "'")
    value = re.sub(r"[{}\\]", " ", value)
    value = value.replace("[", "(").replace("]", ")")
    value = value.replace("|", " / ")
    value = re.sub(r"\s+", " ", value).strip()
    return value[:180] if value else "node"


def quote_mermaid_label(label: str) -> str:
    return '"' + sanitize_mermaid_label(label).replace("\\", "\\\\").replace('"', "'") + '"'


def sanitize_mermaid_code(code: str) -> str:
    def repl_square(match: re.Match[str]) -> str:
        node_id = match.group(1)
        return f"{node_id}[{quote_mermaid_label(match.group(2))}]"

    def repl_curly(match: re.Match[str]) -> str:
        node_id = match.group(1)
        return f"{node_id}{{{quote_mermaid_label(match.group(2))}}}"

    text = code.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"([A-Za-z_][A-Za-z0-9_]*)\[([^\]\n]+)\]", repl_square, text)
    text = re.sub(r"([A-Za-z_][A-Za-z0-9_]*)\{([^}\n]+)\}", repl_curly, text)
    text = re.sub(r"\|([^|\n]+)\|", lambda m: "|" + sanitize_mermaid_label(m.group(1)) + "|", text)
    return text


def sanitize_markdown_mermaid(markdown: str) -> str:
    lines = markdown.splitlines()
    out: List[str] = []
    in_mermaid = False
    buffer: List[str] = []
    for line in lines:
        stripped = line.strip().lower()
        if not in_mermaid and stripped.startswith("```mermaid"):
            in_mermaid = True
            out.append(line)
            buffer = []
            continue
        if in_mermaid and stripped.startswith("```"):
            out.extend(sanitize_mermaid_code("\n".join(buffer)).splitlines())
            out.append(line)
            in_mermaid = False
            buffer = []
            continue
        if in_mermaid:
            buffer.append(line)
        else:
            out.append(line)
    if in_mermaid:
        out.extend(sanitize_mermaid_code("\n".join(buffer)).splitlines())
    return "\n".join(out) + ("\n" if markdown.endswith("\n") else "")


def missing_vendor_files(vendor_dir: Path) -> List[str]:
    return [rel for rel in REQUIRED_VENDOR_FILES if not (vendor_dir / rel).exists()]


def _load_vendor_helper() -> object:
    helper_path = TOOLS_DIR / "vendor_docsite_libs.py"
    spec = importlib.util.spec_from_file_location("_tcad_vendor_docsite_libs", helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load vendor helper: {helper_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ensure_vendor(vendor_dir: Path, *, auto_download: bool = True, clean_vendor: bool = False) -> None:
    missing = missing_vendor_files(vendor_dir)
    if missing and auto_download:
        helper = _load_vendor_helper()
        vendor_libs = getattr(helper, "vendor_libs", None)
        if not callable(vendor_libs):
            raise RuntimeError(f"vendor helper missing vendor_libs(): {TOOLS_DIR / 'vendor_docsite_libs.py'}")
        print("docsite vendor missing; downloading to " + str(vendor_dir))
        vendor_libs(vendor_dir, clean=clean_vendor)
        missing = missing_vendor_files(vendor_dir)
    if missing:
        raise RuntimeError(
            "missing docsite vendor files: "
            + ", ".join(missing)
            + f". Run: python3 {TOOLS_DIR / 'vendor_docsite_libs.py'} --clean"
        )


def copy_vendor(vendor_dir: Path, out_dir: Path) -> None:
    lib_dir = out_dir / "lib"
    if lib_dir.exists():
        shutil.rmtree(lib_dir)
    for rel in VENDOR_COPY_ITEMS:
        src = vendor_dir / rel
        dest = lib_dir / rel
        if src.is_dir():
            shutil.copytree(src, dest, ignore=shutil.ignore_patterns("*.tgz", "__pycache__", ".DS_Store"))
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)


def site_css() -> str:
    return r"""
:root {
  color-scheme: light;
  --bg: #f6f8fb;
  --paper: #ffffff;
  --ink: #202631;
  --muted: #677283;
  --line: #d9e0ea;
  --line-strong: #b8c3d2;
  --accent: #0b6bcb;
  --accent-2: #00856f;
  --code-bg: #f1f5f9;
  --nav-bg: #111827;
  --nav-ink: #f9fafb;
  --nav-muted: #aab4c5;
  --radius: 8px;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
/* MathJax SVG avoids browser font-box clipping and is stable in local files. */
mjx-container[jax="SVG"] { overflow: visible; max-width: 100%; line-height: normal; }
mjx-container[jax="SVG"] > svg { overflow: visible; max-width: 100%; }
mjx-container[jax="SVG"][display="true"] { display: block; overflow-x: auto; overflow-y: visible; padding: 0.35em 0; margin: 0.85em 0; }
mjx-container[jax="SVG"]:not([display="true"]) { display: inline-block; vertical-align: -0.18em; padding: 0.1em 0; }
li:has(mjx-container), td:has(mjx-container), p:has(mjx-container) { line-height: 1.85; }
body { margin: 0; background: var(--bg); color: var(--ink); line-height: 1.65; }
article { overflow-wrap: anywhere; word-break: normal; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.layout { display: grid; grid-template-columns: 300px minmax(0, 1fr) 260px; min-height: 100vh; }
.sidebar { background: var(--nav-bg); color: var(--nav-ink); padding: 24px 18px; position: sticky; top: 0; height: 100vh; overflow: auto; }
.brand { font-weight: 750; font-size: 18px; letter-spacing: 0; margin-bottom: 4px; }
.subtitle { color: var(--nav-muted); font-size: 13px; margin-bottom: 18px; }
.search { width: 100%; border: 1px solid #344154; border-radius: var(--radius); padding: 10px 12px; background: #0b1220; color: var(--nav-ink); margin-bottom: 16px; }
.nav-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; }
.nav-list a { display: block; padding: 8px 10px; border-radius: 6px; color: var(--nav-muted); text-decoration: none; font-size: 14px; }
.nav-list a.active, .nav-list a:hover { color: var(--nav-ink); background: #1f2937; }
.main { min-width: 0; padding: 36px 44px 80px; }
.paper { background: var(--paper); border: 1px solid var(--line); border-radius: var(--radius); padding: 34px 42px; box-shadow: 0 12px 28px rgba(15, 23, 42, 0.07); }
.topbar { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 16px; color: var(--muted); font-size: 13px; }
.source-link { border: 1px solid var(--line); border-radius: 6px; padding: 6px 10px; background: #fff; }
.toc { border-left: 1px solid var(--line); background: #fbfcfe; padding: 24px 16px; position: sticky; top: 0; height: 100vh; overflow: auto; }
.toc-title { font-weight: 700; font-size: 13px; color: var(--muted); text-transform: uppercase; margin-bottom: 10px; }
.toc a { display: block; color: var(--muted); font-size: 13px; padding: 4px 0; text-decoration: none; }
.toc a.level-2 { padding-left: 10px; }
.toc a.level-3 { padding-left: 20px; }
.toc a:hover { color: var(--accent); }
article h1 { margin-top: 0; font-size: 34px; line-height: 1.18; letter-spacing: 0; }
article h2 { margin-top: 34px; border-top: 1px solid var(--line); padding-top: 22px; font-size: 24px; letter-spacing: 0; }
article h3 { margin-top: 26px; font-size: 19px; letter-spacing: 0; }
article h4 { font-size: 16px; letter-spacing: 0; }
article p, article li { font-size: 15px; overflow: visible; overflow-wrap: anywhere; word-break: normal; }
article table { width: 100%; border-collapse: collapse; display: block; overflow-x: auto; margin: 18px 0; }
article th, article td { border: 1px solid var(--line); padding: 10px 10px; vertical-align: top; min-width: 120px; overflow-wrap: anywhere; word-break: normal; }
article th { background: #eef3f8; text-align: left; }
article tr:nth-child(even) td { background: #fbfdff; }
article code { background: var(--code-bg); border: 1px solid #e2e8f0; border-radius: 4px; padding: 0.12em 0.34em; font-size: 0.92em; overflow-wrap: anywhere; word-break: break-word; white-space: normal; }
article pre { position: relative; background: #f8fafc; border: 1px solid var(--line); border-radius: var(--radius); padding: 16px; overflow: auto; }
article pre code { background: transparent; border: 0; padding: 0; }
.copy-code { position: absolute; top: 8px; right: 8px; border: 1px solid var(--line); border-radius: 5px; background: #fff; color: var(--muted); padding: 3px 8px; font-size: 12px; cursor: pointer; }
.mermaid { display: flex; justify-content: center; overflow-x: auto; background: #fbfdff; border: 1px solid var(--line); border-radius: var(--radius); padding: 16px; margin: 18px 0; }
.mermaid-error { display: block; white-space: pre-wrap; background: #fff7ed; border-color: #f97316; color: #7c2d12; }
.doc-meta { color: var(--muted); font-size: 13px; margin-bottom: 20px; }
blockquote { border-left: 4px solid var(--accent-2); padding: 8px 16px; background: #f5fbfa; color: #26423d; margin-left: 0; }
.mobile-title { display: none; padding: 12px 16px; background: var(--nav-bg); color: var(--nav-ink); font-weight: 700; }
@media (max-width: 1180px) {
  .layout { grid-template-columns: 280px minmax(0, 1fr); }
  .toc { display: none; }
}
@media (max-width: 820px) {
  .mobile-title { display: block; }
  .layout { display: block; }
  .sidebar { position: relative; height: auto; }
  .main { padding: 18px 12px 48px; }
  .paper { padding: 22px 18px; }
  article h1 { font-size: 28px; }
}
@media print {
  .sidebar, .toc, .mobile-title, .topbar { display: none; }
  .layout { display: block; }
  .main { padding: 0; }
  .paper { box-shadow: none; border: 0; }
}
"""


def site_js() -> str:
    return r"""
(function () {
  function slugify(text) {
    return String(text || '').trim().toLowerCase()
      .replace(/[^\w\u4e00-\u9fff-]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'section';
  }

  function rewriteMarkdownLinks(root) {
    var relRoot = window.TCAD_DOC_REL_ROOT || '';
    root.querySelectorAll('a[href]').forEach(function (a) {
      var href = a.getAttribute('href') || '';
      if (/\.md(#.*)?$/i.test(href)) {
        var parts = href.split('#');
        var name = parts[0].split('/').pop().replace(/\.md$/i, '').toLowerCase().replace(/[^0-9a-z\u4e00-\u9fff_-]+/g, '-');
        a.setAttribute('href', relRoot + (name === 'readme' ? 'index.html' : 'pages/' + name + '.html') + (parts[1] ? '#' + parts[1] : ''));
      }
    });
  }

  function renderMarkdown() {
    var source = document.getElementById('markdown-source');
    var target = document.getElementById('content');
    if (!source || !target || !window.marked) return;
    var markdown = '';
    try {
      markdown = JSON.parse(source.textContent || '""');
    } catch (err) {
      markdown = source.textContent || '';
    }
    marked.setOptions({
      gfm: true,
      breaks: false,
      mangle: false,
      headerIds: false
    });
    target.innerHTML = marked.parse(markdown);
    var ids = {};
    target.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach(function (h) {
      var base = slugify(h.textContent);
      var id = base;
      var i = 2;
      while (ids[id]) id = base + '-' + (i++);
      ids[id] = true;
      h.id = id;
    });
    target.querySelectorAll('pre code.language-mermaid').forEach(function (code) {
      var div = document.createElement('div');
      div.className = 'mermaid';
      div.textContent = code.textContent;
      code.closest('pre').replaceWith(div);
    });
    target.querySelectorAll('pre').forEach(function (pre) {
      var btn = document.createElement('button');
      btn.className = 'copy-code';
      btn.type = 'button';
      btn.textContent = 'Copy';
      btn.addEventListener('click', function () {
        navigator.clipboard.writeText(pre.innerText || '').then(function () {
          btn.textContent = 'Copied';
          setTimeout(function () { btn.textContent = 'Copy'; }, 1200);
        });
      });
      pre.appendChild(btn);
    });
    rewriteMarkdownLinks(target);
  }

  function renderMermaid() {
    if (!window.mermaid) return Promise.resolve();
    mermaid.initialize({ startOnLoad: false, securityLevel: 'loose', theme: 'default' });
    var blocks = Array.prototype.slice.call(document.querySelectorAll('.mermaid'));
    return Promise.all(blocks.map(function (block, index) {
      var source = block.textContent || '';
      return mermaid.render('mermaid-' + Date.now() + '-' + index, source).then(function (res) {
        block.innerHTML = res.svg;
      }).catch(function (err) {
        block.classList.add('mermaid-error');
        block.textContent = 'Mermaid render failed: ' + String(err && (err.message || err)) + '\n\n' + source;
        console.error('Mermaid render failed', err, source);
      });
    })).catch(function (err) {
      console.error('Mermaid batch render failed', err);
    });
  }

  function highlightCode() {
    if (!window.hljs) return;
    document.querySelectorAll('pre code:not(.language-mermaid)').forEach(function (block) {
      hljs.highlightElement(block);
    });
  }

  function typesetMath() {
    if (window.MathJax && MathJax.typesetPromise) {
      return MathJax.typesetPromise([document.getElementById('content')]).catch(function (err) {
        console.error('MathJax render failed', err);
      });
    }
    return Promise.resolve();
  }

  function setupSearch() {
    var input = document.getElementById('doc-search');
    if (!input) return;
    input.addEventListener('input', function () {
      var q = input.value.trim().toLowerCase();
      document.querySelectorAll('[data-doc-title]').forEach(function (item) {
        var hay = item.getAttribute('data-doc-title').toLowerCase();
        item.style.display = hay.indexOf(q) >= 0 ? '' : 'none';
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    renderMarkdown();
    highlightCode();
    setupSearch();
    typesetMath().then(renderMermaid);
  });
})();
"""


def render_nav(docs: Sequence[Tuple[Path, str, str]], current: str, *, rel_root: str) -> str:
    items = []
    for path, slug, title in docs:
        href = f"{rel_root}index.html" if slug == "readme" else f"{rel_root}pages/{slug}.html"
        active = " active" if slug == current else ""
        items.append(
            f'<li data-doc-title="{html.escape(title)}"><a class="{active.strip()}" href="{html.escape(href)}">{html.escape(title)}</a></li>'
        )
    return "\n".join(items)


def render_toc(headings: Sequence[Tuple[int, str, str]]) -> str:
    if not headings:
        return '<div class="toc-title">On This Page</div><span class="doc-meta">No headings</span>'
    links = ['<div class="toc-title">On This Page</div>']
    for level, text, slug in headings:
        if level > 3:
            continue
        links.append(f'<a class="level-{level}" href="#{html.escape(slug)}">{html.escape(text)}</a>')
    return "\n".join(links)


def page_html(
    *,
    title: str,
    markdown: str,
    nav_html: str,
    toc_html: str,
    rel_root: str,
    source_href: str,
) -> str:
    markdown_json = json.dumps(markdown, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} - TCAD Simulator Docs</title>
  <link rel="stylesheet" href="{rel_root}lib/highlight/styles/github.min.css">
  <link rel="stylesheet" href="{rel_root}assets/site.css">
  <script src="{rel_root}lib/marked/marked.umd.js"></script>
  <script src="{rel_root}lib/highlight/highlight.min.js"></script>
  <script src="{rel_root}lib/mermaid/mermaid.min.js"></script>
  <script>
    window.MathJax = {{
      tex: {{
        inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
        displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']],
        processEscapes: true
      }},
      loader: {{
        paths: {{
          mathjax: '{rel_root}lib/mathjax',
          fonts: '{rel_root}lib/mathjax-newcm-font'
        }}
      }},
      options: {{ skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'] }},
      startup: {{ typeset: false }}
    }};
  </script>
  <script defer src="{rel_root}lib/mathjax/tex-mml-svg.js"></script>
  <script>window.TCAD_DOC_REL_ROOT = '{rel_root}';</script>
  <script defer src="{rel_root}assets/site.js"></script>
</head>
<body>
  <div class="mobile-title">TCAD Simulator Docs</div>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand">TCAD Simulator</div>
      <div class="subtitle">TCAD simulator documentation</div>
      <input id="doc-search" class="search" type="search" placeholder="Search docs">
      <ul class="nav-list">
        {nav_html}
      </ul>
    </aside>
    <main class="main">
      <div class="topbar">
        <span>Generated offline documentation site</span>
        <a class="source-link" href="{html.escape(source_href)}">View Markdown</a>
      </div>
      <article class="paper">
        <div id="content"></div>
      </article>
    </main>
    <aside class="toc">
      {toc_html}
    </aside>
  </div>
  <script id="markdown-source" type="application/json">{markdown_json}</script>
</body>
</html>
"""


def build_docsite(
    docs_dir: Path,
    out_dir: Path,
    *,
    vendor_dir: Path = DEFAULT_VENDOR_DIR,
    auto_vendor: bool = True,
    clean_vendor: bool = False,
) -> None:
    docs_dir = docs_dir.resolve()
    out_dir = out_dir.resolve()
    vendor_dir = vendor_dir.resolve()
    if not docs_dir.exists():
        raise RuntimeError(f"docs dir does not exist: {docs_dir}")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ensure_vendor(vendor_dir, auto_download=auto_vendor, clean_vendor=clean_vendor)
    copy_vendor(vendor_dir, out_dir)
    write_text(out_dir / "assets" / "site.css", site_css().strip() + "\n")
    write_text(out_dir / "assets" / "site.js", site_js().strip() + "\n")

    docs_paths = docs_sorted(docs_dir)
    docs_meta: List[Tuple[Path, str, str]] = []
    contents: Dict[str, str] = {}
    for path in docs_paths:
        markdown = sanitize_markdown_mermaid(path.read_text(encoding="utf-8"))
        slug = page_slug(path)
        title = html_title(markdown, path.stem.replace("_", " ").title())
        docs_meta.append((path, slug, title))
        contents[path.name] = markdown
        write_text(out_dir / "source_md" / path.name, markdown)

    for path, slug, title in docs_meta:
        markdown = contents[path.name]
        headings = extract_headings(markdown)
        is_home = slug == "readme"
        rel_root = "" if is_home else "../"
        source_href = f"{rel_root}source_md/{path.name}"
        nav_html = render_nav(docs_meta, slug, rel_root=rel_root)
        toc_html = render_toc(headings)
        html_text = page_html(
            title=title,
            markdown=markdown,
            nav_html=nav_html,
            toc_html=toc_html,
            rel_root=rel_root,
            source_href=source_href,
        )
        dest = out_dir / "index.html" if is_home else out_dir / "pages" / f"{slug}.html"
        write_text(dest, html_text)

    manifest = {
        "source_docs": [p.name for p in docs_paths],
        "pages": [
            {"markdown": p.name, "slug": slug, "title": title, "html": "index.html" if slug == "readme" else f"pages/{slug}.html"}
            for p, slug, title in docs_meta
        ],
        "vendor_files": REQUIRED_VENDOR_FILES,
    }
    write_text(out_dir / "DOCSITE_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def verify_docsite(out_dir: Path) -> None:
    out_dir = out_dir.resolve()
    required = [
        out_dir / "index.html",
        out_dir / "assets" / "site.css",
        out_dir / "assets" / "site.js",
        out_dir / "DOCSITE_MANIFEST.json",
    ] + [out_dir / "lib" / rel for rel in REQUIRED_VENDOR_FILES]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise RuntimeError("missing docsite outputs: " + ", ".join(missing))
    bad_refs: List[str] = []
    for path in out_dir.rglob("*.html"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r'<script[^>]+src=["\']https?://|<link[^>]+href=["\']https?://', text, flags=re.IGNORECASE):
            bad_refs.append(str(path))
    if bad_refs:
        raise RuntimeError("docsite contains CDN references: " + ", ".join(bad_refs))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build offline HTML documentation site from Markdown docs.")
    parser.add_argument("--docs-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--vendor-dir", default=str(DEFAULT_VENDOR_DIR))
    parser.add_argument("--no-auto-vendor", action="store_true", help="Fail if vendor files are missing instead of downloading them.")
    parser.add_argument("--clean-vendor", action="store_true", help="Redownload the documentation vendor cache before building.")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if args.verify:
        verify_docsite(out_dir)
        print(f"docsite ok: {out_dir}")
        return 0
    build_docsite(
        Path(args.docs_dir).expanduser().resolve(),
        out_dir,
        vendor_dir=Path(args.vendor_dir).expanduser().resolve(),
        auto_vendor=not bool(args.no_auto_vendor),
        clean_vendor=bool(args.clean_vendor),
    )
    verify_docsite(out_dir)
    print(f"docsite built: {out_dir / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
