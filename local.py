# local.py — 本地管理工具 v2
# 支持：分页文件统一展示、编辑、删除、置顶同步到 arktips.json

from flask import Flask, request, redirect, jsonify, render_template_string
import json
from pathlib import Path
from datetime import datetime
import shutil
import webbrowser
import threading
import subprocess
import urllib.parse
import re

app = Flask(__name__)

BASE_DIR   = Path(__file__).resolve().parent
BACKUP_DIR = BASE_DIR / "json_backups"

ANN_FILE     = BASE_DIR / "announcements.json"
ARKTIPS_FILE = BASE_DIR / "arktips.json"   # 只存置顶
PAGE_PREFIX  = "arktips-"                  # arktips-1.json, arktips-2.json ...
PAGE_SIZE    = 100


# ──────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────
def backup(path: Path):
    BACKUP_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(path, BACKUP_DIR / f"{path.stem}_backup_{ts}.json")


def load_json(path: Path) -> list | dict:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_json(path: Path, data):
    backup(path)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_bool(value) -> bool:
    return value in ("on", "true", "True", "1", 1, True)


def parse_pin_order(value) -> int:
    try:
        n = int(str(value or "").strip())
        return n if n > 0 else 999999
    except ValueError:
        return 999999


# ──────────────────────────────────────────────────────────────
# 分页文件管理
# ──────────────────────────────────────────────────────────────
def get_page_files() -> list[Path]:
    """返回所有分页文件，按页码排序"""
    files = []
    for f in BASE_DIR.glob(f"{PAGE_PREFIX}*.json"):
        m = re.match(r'arktips-(\d+)\.json', f.name)
        if m:
            files.append((int(m.group(1)), f))
    files.sort(key=lambda x: x[0])
    return [f for _, f in files]


def load_page(path: Path) -> list:
    data = load_json(path)
    return data if isinstance(data, list) else []


def find_item_page(item_id) -> tuple[Path | None, int]:
    """根据 id 找到条目在哪个分页文件的哪个位置"""
    for page_file in get_page_files():
        items = load_page(page_file)
        for i, item in enumerate(items):
            if str(item.get("id")) == str(item_id):
                return page_file, i
    return None, -1


# ──────────────────────────────────────────────────────────────
# arktips.json 置顶管理（只存置顶）
# ──────────────────────────────────────────────────────────────
def arktips_upsert(item: dict):
    """把条目写入/更新 arktips.json（置顶列表）"""
    data = load_json(ARKTIPS_FILE)
    if not isinstance(data, list):
        data = []
    # 去重
    data = [e for e in data if str(e.get("id")) != str(item.get("id"))]
    data.insert(0, item)
    save_json(ARKTIPS_FILE, data)


def arktips_remove(item_id):
    """从 arktips.json 删除指定条目"""
    data = load_json(ARKTIPS_FILE)
    if not isinstance(data, list):
        return
    data = [e for e in data if str(e.get("id")) != str(item_id)]
    save_json(ARKTIPS_FILE, data)


# ──────────────────────────────────────────────────────────────
# Git 操作
# ──────────────────────────────────────────────────────────────
def run_cmd(args, cwd=None):
    result = subprocess.run(
        args, cwd=str(cwd or BASE_DIR),
        capture_output=True, text=True, shell=False
    )
    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    return result.returncode == 0, output


def get_current_branch():
    ok, out = run_cmd(["git", "branch", "--show-current"])
    branch = out.strip() if ok and out else ""
    return branch if branch and branch != "HEAD" else "main"


def ensure_gitignore():
    gitignore = BASE_DIR / ".gitignore"
    line = "json_backups/"
    if gitignore.exists():
        text = gitignore.read_text(encoding="utf-8", errors="ignore")
        if line not in [x.strip() for x in text.splitlines()]:
            with gitignore.open("a", encoding="utf-8") as f:
                if text and not text.endswith("\n"):
                    f.write("\n")
                f.write(line + "\n")
    else:
        gitignore.write_text(line + "\n", encoding="utf-8")


def git_push():
    ensure_gitignore()
    branch = get_current_branch()
    ok, out = run_cmd(["git", "add", "."])
    if not ok:
        return False, "git add 失败：" + out
    msg = f"Update {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    run_cmd(["git", "commit", "-m", msg])
    ok2, out2 = run_cmd(["git", "pull", "--rebase", "origin", branch])
    if not ok2:
        return False, "git pull 失败：" + out2
    ok3, out3 = run_cmd(["git", "push", "origin", f"HEAD:refs/heads/{branch}"])
    if not ok3:
        return False, "git push 失败：" + out3
    return True, f"已推送到 origin/{branch}"


def git_pull():
    branch = get_current_branch()
    ensure_gitignore()
    run_cmd(["git", "add", "."])
    run_cmd(["git", "commit", "-m", f"local save {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"])
    ok, out = run_cmd(["git", "pull", "--rebase", "origin", branch])
    return ok, out


# ──────────────────────────────────────────────────────────────
# HTML 模板
# ──────────────────────────────────────────────────────────────
TEMPLATE = r"""
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>本地管理 · Local Manager</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0a14;color:#dde;font-family:'Segoe UI',Arial,sans-serif;font-size:14px;}
.topbar{position:sticky;top:0;z-index:100;background:rgba(8,5,28,.95);border-bottom:1px solid rgba(180,126,255,.2);padding:10px 20px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;backdrop-filter:blur(12px);}
.topbar h1{font-size:1em;color:#b47eff;flex-shrink:0;}
.tab-btn{padding:5px 14px;border-radius:999px;border:1px solid rgba(180,126,255,.3);background:transparent;color:#b47eff;cursor:pointer;font-size:12px;transition:all .2s;}
.tab-btn.active,.tab-btn:hover{background:rgba(180,126,255,.15);border-color:#b47eff;}
.git-btn{padding:5px 14px;border-radius:999px;border:1px solid rgba(0,229,255,.3);background:transparent;color:#00e5ff;cursor:pointer;font-size:12px;transition:all .2s;margin-left:auto;}
.git-btn:hover{background:rgba(0,229,255,.1);}
.git-btn.push{border-color:rgba(74,222,128,.3);color:#4ade80;}
.git-btn.push:hover{background:rgba(74,222,128,.1);}
.msg{padding:8px 20px;font-size:12px;border-bottom:1px solid rgba(255,255,255,.06);}
.msg.success{color:#4ade80;background:rgba(74,222,128,.06);}
.msg.warning{color:#fbbf24;background:rgba(251,191,36,.06);}
.msg.error{color:#f87171;background:rgba(248,113,113,.06);}
.container{max-width:900px;margin:0 auto;padding:16px;}

/* 新增表单 */
.add-form{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.08);border-radius:10px;padding:16px;margin-bottom:20px;}
.add-form h2{font-size:.9em;color:#888;margin-bottom:12px;letter-spacing:.08em;}
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;}
.form-full{grid-column:1/-1;}
.form-row{display:flex;flex-direction:column;gap:4px;}
label{font-size:11px;color:#666;letter-spacing:.06em;}
input[type=text],input[type=date],textarea,select{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.1);border-radius:6px;color:#dde;padding:7px 10px;font-size:13px;width:100%;outline:none;transition:border-color .2s;}
input:focus,textarea:focus,select:focus{border-color:rgba(180,126,255,.5);}
textarea{resize:vertical;min-height:70px;font-family:inherit;}
.checkbox-row{display:flex;align-items:center;gap:8px;padding:4px 0;}
.checkbox-row input[type=checkbox]{width:16px;height:16px;accent-color:#b47eff;}
.checkbox-row label{font-size:13px;color:#aab;cursor:pointer;}
.btn{padding:7px 18px;border-radius:6px;border:none;cursor:pointer;font-size:13px;transition:all .2s;}
.btn-primary{background:#b47eff;color:#0a0a14;font-weight:600;}
.btn-primary:hover{background:#c87fff;}
.btn-sm{padding:3px 10px;font-size:11px;border-radius:4px;}
.btn-edit{background:rgba(0,229,255,.12);color:#00e5ff;border:1px solid rgba(0,229,255,.25);}
.btn-edit:hover{background:rgba(0,229,255,.2);}
.btn-delete{background:rgba(248,113,113,.12);color:#f87171;border:1px solid rgba(248,113,113,.25);}
.btn-delete:hover{background:rgba(248,113,113,.2);}
.btn-pin{background:rgba(255,210,90,.12);color:#ffd76a;border:1px solid rgba(255,210,90,.25);}
.btn-pin:hover{background:rgba(255,210,90,.2);}
.btn-unpin{background:rgba(180,126,255,.12);color:#b47eff;border:1px solid rgba(180,126,255,.25);}
.btn-unpin:hover{background:rgba(180,126,255,.2);}

/* 列表 */
.list-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;}
.list-count{font-size:12px;color:#555;}
.item-card{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);border-radius:8px;padding:12px 14px;margin-bottom:8px;transition:border-color .2s;}
.item-card:hover{border-color:rgba(180,126,255,.2);}
.item-card.is-pinned{border-color:rgba(255,210,90,.3);background:rgba(255,210,90,.03);}
.item-top{display:flex;align-items:flex-start;gap:8px;margin-bottom:6px;}
.item-num{font-size:10px;color:#444;flex-shrink:0;padding-top:2px;min-width:28px;}
.item-title{font-size:13px;color:#dde;flex:1;line-height:1.5;word-break:break-word;}
.item-badges{display:flex;gap:5px;flex-wrap:wrap;margin-left:auto;flex-shrink:0;}
.badge{display:inline-flex;align-items:center;padding:1px 7px;border-radius:999px;font-size:10px;border:1px solid currentColor;}
.badge-pin{color:#ffd76a;}
.badge-cat{color:#b47eff;}
.badge-ch{color:#6ee7b7;}
.item-meta{font-size:11px;color:#444;margin-bottom:8px;}
.item-content{font-size:12px;color:#667;background:rgba(255,255,255,.02);border-left:2px solid rgba(255,255,255,.06);padding:6px 10px;border-radius:0 4px 4px 0;margin-bottom:8px;white-space:pre-wrap;max-height:80px;overflow:hidden;}
.item-content.expanded{max-height:none;}
.expand-btn{font-size:10px;color:#555;cursor:pointer;background:none;border:none;padding:2px 4px;}
.expand-btn:hover{color:#b47eff;}
.item-images{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px;}
.item-img{width:60px;height:60px;object-fit:cover;border-radius:5px;border:1px solid rgba(255,255,255,.08);}
.item-actions{display:flex;gap:6px;flex-wrap:wrap;}
.page-label{font-size:10px;color:#333;padding:4px 0 8px;letter-spacing:.04em;}

/* 编辑弹窗 */
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:500;overflow-y:auto;}
.modal-overlay.show{display:flex;align-items:flex-start;justify-content:center;padding:40px 16px;}
.modal{background:#0e0e1e;border:1px solid rgba(180,126,255,.3);border-radius:12px;padding:24px;width:100%;max-width:600px;}
.modal h2{font-size:1em;color:#b47eff;margin-bottom:16px;}
.modal-actions{display:flex;gap:8px;margin-top:16px;flex-wrap:wrap;}
.btn-cancel{background:rgba(255,255,255,.06);color:#888;border:1px solid rgba(255,255,255,.1);}
.btn-cancel:hover{background:rgba(255,255,255,.1);}

/* 无限滚动 */
.sentinel{height:40px;display:flex;align-items:center;justify-content:center;color:#333;font-size:11px;}
.sentinel.loading::after{content:'';width:16px;height:16px;border:2px solid rgba(180,126,255,.2);border-top-color:#b47eff;border-radius:50%;animation:spin .8s linear infinite;display:inline-block;}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>

<div class="topbar">
  <h1>📋 Local Manager</h1>
  <button class="tab-btn active" onclick="switchTab('arktips')">资源区</button>
  <button class="tab-btn" onclick="switchTab('announcements')">公告</button>
  <form method="post" action="/pull" style="display:inline" onsubmit="return confirm('拉取远程？')">
    <button class="git-btn" type="submit">⬇ Pull</button>
  </form>
  <form method="post" action="/push" style="display:inline" onsubmit="return confirm('推送到 GitHub？')">
    <button class="git-btn push" type="submit">⬆ Push</button>
  </form>
</div>

{% if message %}
<div class="msg {{ message_type }}">{{ message }}</div>
{% endif %}

<div class="container">

  <!-- 新增表单 -->
  <div class="add-form" id="addForm">
    <h2>＋ 新增条目（{{ 'arktips' if tab == 'arktips' else 'announcements' }}）</h2>
    <form method="post" action="/add?tab={{ tab }}">
      {% if tab == 'arktips' %}
      <div class="form-grid">
        <div class="form-row"><label>频道</label><input type="text" name="channel" placeholder="@ARKTIPS"></div>
        <div class="form-row"><label>日期</label><input type="date" name="date" value="{{ today }}"></div>
        <div class="form-row form-full"><label>标题</label><input type="text" name="title" placeholder="标题（留空则取文本前50字）"></div>
        <div class="form-row form-full"><label>文本内容</label><textarea name="text" rows="3" placeholder="消息正文"></textarea></div>
        <div class="form-row form-full"><label>图片链接（每行一个）</label><textarea name="images" rows="2" placeholder="https://..."></textarea></div>
        <div class="form-row"><label>分类</label>
          <select name="category">
            <option value="活动">活动</option>
            <option value="资源更新">资源更新</option>
            <option value="其他">其他</option>
          </select>
        </div>
        <div class="form-row"><label>置顶顺序</label><input type="text" name="pinOrder" placeholder="留空=不置顶"></div>
        <div class="form-row"><label>截止日期</label><input type="date" name="pinExpiry"></div>
        <div class="form-row form-full checkbox-row">
          <input type="checkbox" name="important" id="imp_new">
          <label for="imp_new">📌 置顶（勾选后自动同步到 arktips.json）</label>
        </div>
      </div>
      {% else %}
      <div class="form-grid">
        <div class="form-row form-full"><label>标题</label><input type="text" name="title" placeholder="公告标题"></div>
        <div class="form-row"><label>日期</label><input type="date" name="date" value="{{ today }}"></div>
        <div class="form-row"><label>分类</label>
          <select name="category">
            <option value="重要">重要</option>
            <option value="更新">更新</option>
            <option value="维护">维护</option>
            <option value="活动">活动</option>
            <option value="其他">其他</option>
          </select>
        </div>
        <div class="form-row form-full"><label>内容</label><textarea name="content" rows="3"></textarea></div>
        <div class="form-row form-full"><label>图片链接</label><input type="text" name="image" placeholder="https://..."></div>
        <div class="form-row"><label>置顶顺序</label><input type="text" name="pinOrder" placeholder="留空=不置顶"></div>
        <div class="form-row"><label>截止日期</label><input type="date" name="pinExpiry"></div>
        <div class="form-row form-full checkbox-row">
          <input type="checkbox" name="important" id="imp_new2">
          <label for="imp_new2">📌 置顶</label>
        </div>
      </div>
      {% endif %}
      <div style="margin-top:12px"><button class="btn btn-primary" type="submit">保存</button></div>
    </form>
  </div>

  <!-- 列表 -->
  <div class="list-header">
    <span class="list-count" id="listCount">加载中...</span>
  </div>
  <div id="itemList"></div>
  <div class="sentinel" id="sentinel"></div>
</div>

<!-- 编辑弹窗 -->
<div class="modal-overlay" id="modalOverlay">
  <div class="modal">
    <h2>✏️ 编辑条目</h2>
    <form method="post" id="editForm" action="">
      <input type="hidden" name="tab" id="editTab">
      <input type="hidden" name="item_id" id="editItemId">
      <input type="hidden" name="page_file" id="editPageFile">
      <div class="form-grid" id="editFields"></div>
      <div class="modal-actions">
        <button class="btn btn-primary" type="submit">保存</button>
        <button class="btn btn-cancel" type="button" onclick="closeModal()">取消</button>
      </div>
    </form>
  </div>
</div>

<script>
let currentTab = '{{ tab }}';
let allItems   = [];   // 全部数据（懒加载）
let rendered   = 0;
const BATCH    = 30;
let isLoading  = false;

// ── 切换 Tab ──
function switchTab(tab) {
  window.location.href = '/?tab=' + tab;
}

// ── 加载数据 ──
async function loadData() {
  const r = await fetch('/api/items?tab=' + currentTab);
  const d = await r.json();
  allItems = d.items || [];
  rendered = 0;
  document.getElementById('itemList').innerHTML = '';
  document.getElementById('listCount').textContent = `共 ${allItems.length} 条`;
  renderBatch();
}

// ── 渲染一批 ──
function renderBatch() {
  if (rendered >= allItems.length) {
    document.getElementById('sentinel').textContent = '— 已全部加载 —';
    document.getElementById('sentinel').classList.remove('loading');
    return;
  }
  const batch = allItems.slice(rendered, rendered + BATCH);
  const list  = document.getElementById('itemList');
  let html = '';
  let lastPage = '';
  batch.forEach((item, i) => {
    const idx = rendered + i;
    if (item._page && item._page !== lastPage) {
      html += `<div class="page-label">── ${item._page} ──</div>`;
      lastPage = item._page;
    }
    html += renderCard(item, idx);
  });
  list.insertAdjacentHTML('beforeend', html);
  rendered += batch.length;
}

// ── 渲染卡片 ──
function renderCard(item, idx) {
  const title   = esc(item.title || item.text?.slice(0,60) || '无标题');
  const cat     = esc(item.category || '');
  const ch      = esc(item.channel || '');
  const date    = esc(item.date || item.time || '');
  const content = esc(item.content || item.text || '');
  const pinned  = item.important === true || item.important === 'true' || item.important === 1;
  const imgs    = Array.isArray(item.images) ? item.images.filter(Boolean) : (item.image ? [item.image] : []);

  const pinBadge   = pinned ? `<span class="badge badge-pin">📌 置顶</span>` : '';
  const catBadge   = cat ? `<span class="badge badge-cat">${cat}</span>` : '';
  const chBadge    = ch ? `<span class="badge badge-ch">${ch}</span>` : '';
  const imgHtml    = imgs.slice(0,3).map(u => `<img class="item-img" src="${esc(u)}" onerror="this.style.display='none'">`).join('');
  const contentHtml = content
    ? `<div class="item-content" id="ct-${idx}">${content}</div>
       <button class="expand-btn" onclick="toggleContent(${idx})">展开 ▾</button>`
    : '';

  const pinBtn = pinned
    ? `<button class="btn btn-sm btn-unpin" onclick="togglePin('${esc(item.id)}','${currentTab}',false)">取消置顶</button>`
    : `<button class="btn btn-sm btn-pin" onclick="togglePin('${esc(item.id)}','${currentTab}',true)">📌 置顶</button>`;

  return `
  <div class="item-card ${pinned?'is-pinned':''}" id="card-${idx}">
    <div class="item-top">
      <span class="item-num">${String(idx+1).padStart(3,'0')}</span>
      <span class="item-title">${title}</span>
      <div class="item-badges">${pinBadge}${catBadge}${chBadge}</div>
    </div>
    <div class="item-meta">${date}${item._page?' &nbsp;·&nbsp; '+esc(item._page):''}</div>
    ${contentHtml}
    ${imgHtml ? `<div class="item-images">${imgHtml}</div>` : ''}
    <div class="item-actions">
      <button class="btn btn-sm btn-edit" onclick="openEdit(${idx})">编辑</button>
      ${pinBtn}
      <button class="btn btn-sm btn-delete" onclick="deleteItem('${esc(item.id)}','${currentTab}')">删除</button>
    </div>
  </div>`;
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = String(s ?? '');
  return d.innerHTML;
}

function toggleContent(idx) {
  const el  = document.getElementById(`ct-${idx}`);
  const btn = el?.nextElementSibling;
  if (!el) return;
  el.classList.toggle('expanded');
  if (btn) btn.textContent = el.classList.contains('expanded') ? '收起 ▴' : '展开 ▾';
}

// ── 编辑弹窗 ──
function openEdit(idx) {
  const item = allItems[idx];
  if (!item) return;
  document.getElementById('editTab').value      = currentTab;
  document.getElementById('editItemId').value   = item.id;
  document.getElementById('editPageFile').value = item._page_file || '';
  document.getElementById('editForm').action    = '/update';

  let fields = '';
  if (currentTab === 'arktips') {
    const imgs = Array.isArray(item.images) ? item.images.join('\n') : (item.image || '');
    fields = `
      <div class="form-row"><label>频道</label><input type="text" name="channel" value="${esc(item.channel||'')}"></div>
      <div class="form-row"><label>日期</label><input type="date" name="date" value="${esc(item.date||'')}"></div>
      <div class="form-row form-full"><label>标题</label><input type="text" name="title" value="${esc(item.title||'')}"></div>
      <div class="form-row form-full"><label>文本内容</label><textarea name="text" rows="4">${esc(item.text||item.content||'')}</textarea></div>
      <div class="form-row form-full"><label>图片链接（每行一个）</label><textarea name="images" rows="3">${esc(imgs)}</textarea></div>
      <div class="form-row"><label>分类</label>
        <select name="category">
          <option value="活动" ${item.category==='活动'?'selected':''}>活动</option>
          <option value="资源更新" ${item.category==='资源更新'?'selected':''}>资源更新</option>
          <option value="其他" ${item.category==='其他'?'selected':''}>其他</option>
        </select>
      </div>
      <div class="form-row"><label>置顶顺序</label><input type="text" name="pinOrder" value="${esc(item.pinOrder===999999?'':item.pinOrder)}"></div>
      <div class="form-row"><label>截止日期</label><input type="date" name="pinExpiry" value="${esc(item.pinExpiry||'')}"></div>
      <div class="form-row form-full checkbox-row">
        <input type="checkbox" name="important" id="imp_edit" ${(item.important===true||item.important==='true'||item.important===1)?'checked':''}>
        <label for="imp_edit">📌 置顶（保存后自动同步 arktips.json）</label>
      </div>`;
  } else {
    fields = `
      <div class="form-row form-full"><label>标题</label><input type="text" name="title" value="${esc(item.title||'')}"></div>
      <div class="form-row"><label>日期</label><input type="date" name="date" value="${esc(item.date||'')}"></div>
      <div class="form-row"><label>分类</label>
        <select name="category">
          <option value="重要" ${item.category==='重要'?'selected':''}>重要</option>
          <option value="更新" ${item.category==='更新'?'selected':''}>更新</option>
          <option value="维护" ${item.category==='维护'?'selected':''}>维护</option>
          <option value="活动" ${item.category==='活动'?'selected':''}>活动</option>
          <option value="其他" ${item.category==='其他'?'selected':''}>其他</option>
        </select>
      </div>
      <div class="form-row form-full"><label>内容</label><textarea name="content" rows="4">${esc(item.content||'')}</textarea></div>
      <div class="form-row form-full"><label>图片链接</label><input type="text" name="image" value="${esc(item.image||'')}"></div>
      <div class="form-row"><label>置顶顺序</label><input type="text" name="pinOrder" value="${esc(item.pinOrder===999999?'':item.pinOrder)}"></div>
      <div class="form-row"><label>截止日期</label><input type="date" name="pinExpiry" value="${esc(item.pinExpiry||'')}"></div>
      <div class="form-row form-full checkbox-row">
        <input type="checkbox" name="important" id="imp_edit2" ${(item.important===true||item.important==='true'||item.important===1)?'checked':''}>
        <label for="imp_edit2">📌 置顶</label>
      </div>`;
  }
  document.getElementById('editFields').innerHTML = fields;
  document.getElementById('modalOverlay').classList.add('show');
}

function closeModal() {
  document.getElementById('modalOverlay').classList.remove('show');
}

// ── 快速置顶/取消置顶 ──
async function togglePin(itemId, tab, pin) {
  const r = await fetch('/api/toggle-pin', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({item_id: itemId, tab, pin})
  });
  const d = await r.json();
  if (d.ok) loadData();
  else alert('操作失败：' + d.msg);
}

// ── 删除 ──
async function deleteItem(itemId, tab) {
  if (!confirm('确认删除这条？')) return;
  const r = await fetch('/api/delete', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({item_id: itemId, tab})
  });
  const d = await r.json();
  if (d.ok) loadData();
  else alert('删除失败：' + d.msg);
}

// ── IntersectionObserver 懒加载 ──
const observer = new IntersectionObserver(entries => {
  if (entries[0].isIntersecting && !isLoading) {
    isLoading = true;
    document.getElementById('sentinel').classList.add('loading');
    setTimeout(() => {
      renderBatch();
      isLoading = false;
      document.getElementById('sentinel').classList.remove('loading');
    }, 100);
  }
}, { rootMargin: '200px' });
observer.observe(document.getElementById('sentinel'));

// ── 关闭弹窗点击背景 ──
document.getElementById('modalOverlay').addEventListener('click', function(e) {
  if (e.target === this) closeModal();
});

loadData();
</script>
</body>
</html>
"""


# ──────────────────────────────────────────────────────────────
# 路由
# ──────────────────────────────────────────────────────────────
@app.route("/")
def index():
    tab          = request.args.get("tab", "arktips")
    message      = request.args.get("message", "")
    message_type = request.args.get("type", "success")
    today        = datetime.now().strftime("%Y-%m-%d")
    return render_template_string(
        TEMPLATE,
        tab=tab,
        message=message,
        message_type=message_type,
        today=today,
    )


@app.route("/api/items")
def api_items():
    tab = request.args.get("tab", "arktips")
    items = []

    if tab == "arktips":
        page_files = get_page_files()
        if page_files:
            for pf in page_files:
                page_items = load_page(pf)
                for item in page_items:
                    item["_page"]      = pf.name
                    item["_page_file"] = str(pf)
                    items.append(item)
        else:
            # 降级：读旧 arktips.json
            data = load_json(ARKTIPS_FILE)
            if isinstance(data, list):
                for item in data:
                    item["_page"]      = "arktips.json"
                    item["_page_file"] = str(ARKTIPS_FILE)
                    items.append(item)
    else:
        data = load_json(ANN_FILE)
        if isinstance(data, list):
            for item in data:
                item["_page"]      = "announcements.json"
                item["_page_file"] = str(ANN_FILE)
                items.append(item)

    return jsonify({"items": items, "total": len(items)})


@app.route("/add", methods=["POST"])
def add():
    tab = request.args.get("tab", "arktips")

    if tab == "arktips":
        imgs_raw = request.form.get("images", "").strip()
        imgs     = [x.strip() for x in imgs_raw.splitlines() if x.strip()]
        raw_text = request.form.get("text", "").strip()
        raw_title= request.form.get("title", "").strip()
        important= parse_bool(request.form.get("important"))
        item = {
            "id":        int(datetime.now().timestamp()),
            "channel":   request.form.get("channel", "").strip(),
            "date":      request.form.get("date", "").strip(),
            "time":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "title":     raw_title if raw_title else raw_text[:50],
            "text":      raw_text,
            "content":   raw_text,
            "image":     imgs[0] if imgs else "",
            "images":    imgs,
            "videos":    [],
            "category":  request.form.get("category", "活动").strip(),
            "important": important,
            "pinOrder":  parse_pin_order(request.form.get("pinOrder")),
            "pinExpiry": request.form.get("pinExpiry", "").strip(),
        }
        # 写入当前分页文件
        page_files = get_page_files()
        if page_files:
            last_page = page_files[-1]
            data = load_page(last_page)
            if len(data) >= PAGE_SIZE:
                # 新建下一页
                next_num = len(page_files) + 1
                last_page = BASE_DIR / f"{PAGE_PREFIX}{next_num}.json"
                data = []
            data.insert(0, item)
            save_json(last_page, data)
        else:
            # 没有分页文件，写旧 arktips.json
            data = load_json(ARKTIPS_FILE)
            if not isinstance(data, list):
                data = []
            data.insert(0, item)
            save_json(ARKTIPS_FILE, data)

        # 同步置顶
        if important:
            arktips_upsert(item)

    else:
        important = parse_bool(request.form.get("important"))
        item = {
            "title":     request.form.get("title", "").strip(),
            "date":      request.form.get("date", "").strip(),
            "category":  request.form.get("category", "").strip(),
            "content":   request.form.get("content", "").strip(),
            "image":     request.form.get("image", "").strip(),
            "important": important,
            "pinOrder":  parse_pin_order(request.form.get("pinOrder")),
            "pinExpiry": request.form.get("pinExpiry", "").strip(),
        }
        data = load_json(ANN_FILE)
        if not isinstance(data, list):
            data = []
        data.insert(0, item)
        save_json(ANN_FILE, data)

    msg = urllib.parse.quote("已保存。")
    return redirect(f"/?message={msg}&type=success&tab={tab}")


@app.route("/update", methods=["POST"])
def update():
    tab       = request.form.get("tab", "arktips")
    item_id   = request.form.get("item_id", "")
    page_file = request.form.get("page_file", "")
    important = parse_bool(request.form.get("important"))

    if tab == "arktips":
        if page_file and Path(page_file).exists():
            pf   = Path(page_file)
            data = load_page(pf)
            idx  = next((i for i, e in enumerate(data) if str(e.get("id")) == str(item_id)), -1)
            if idx >= 0:
                imgs_raw = request.form.get("images", "").strip()
                imgs     = [x.strip() for x in imgs_raw.splitlines() if x.strip()]
                raw_text = request.form.get("text", "").strip()
                raw_title= request.form.get("title", "").strip()
                old = data[idx]
                data[idx] = {
                    **old,
                    "channel":   request.form.get("channel", "").strip(),
                    "date":      request.form.get("date", "").strip(),
                    "title":     raw_title if raw_title else raw_text[:50],
                    "text":      raw_text,
                    "content":   raw_text,
                    "image":     imgs[0] if imgs else "",
                    "images":    imgs,
                    "category":  request.form.get("category", "活动").strip(),
                    "important": important,
                    "pinOrder":  parse_pin_order(request.form.get("pinOrder")),
                    "pinExpiry": request.form.get("pinExpiry", "").strip(),
                }
                save_json(pf, data)
                # 同步置顶
                if important:
                    arktips_upsert(data[idx])
                else:
                    arktips_remove(item_id)
    else:
        data = load_json(ANN_FILE)
        if isinstance(data, list):
            idx = next((i for i, e in enumerate(data)
                       if str(e.get("id","")) == str(item_id) or
                          str(e.get("title","")) == request.form.get("title","")), -1)
            if idx >= 0:
                old = data[idx]
                data[idx] = {
                    **old,
                    "title":     request.form.get("title", "").strip(),
                    "date":      request.form.get("date", "").strip(),
                    "category":  request.form.get("category", "").strip(),
                    "content":   request.form.get("content", "").strip(),
                    "image":     request.form.get("image", "").strip(),
                    "important": important,
                    "pinOrder":  parse_pin_order(request.form.get("pinOrder")),
                    "pinExpiry": request.form.get("pinExpiry", "").strip(),
                }
                save_json(ANN_FILE, data)

    msg = urllib.parse.quote("已修改并保存。")
    return redirect(f"/?message={msg}&type=success&tab={tab}")


@app.route("/api/toggle-pin", methods=["POST"])
def api_toggle_pin():
    d       = request.get_json()
    item_id = str(d.get("item_id", ""))
    tab     = d.get("tab", "arktips")
    pin     = d.get("pin", False)

    if tab == "arktips":
        pf, idx = find_item_page(item_id)
        if pf is None:
            return jsonify({"ok": False, "msg": "找不到条目"})
        data = load_page(pf)
        data[idx]["important"] = pin
        save_json(pf, data)
        if pin:
            arktips_upsert(data[idx])
        else:
            arktips_remove(item_id)
    else:
        data = load_json(ANN_FILE)
        if isinstance(data, list):
            for item in data:
                if str(item.get("id","")) == item_id:
                    item["important"] = pin
                    break
            save_json(ANN_FILE, data)

    return jsonify({"ok": True})


@app.route("/api/delete", methods=["POST"])
def api_delete():
    d       = request.get_json()
    item_id = str(d.get("item_id", ""))
    tab     = d.get("tab", "arktips")

    if tab == "arktips":
        pf, idx = find_item_page(item_id)
        if pf is None:
            # 降级：旧 arktips.json
            data = load_json(ARKTIPS_FILE)
            if isinstance(data, list):
                data = [e for e in data if str(e.get("id","")) != item_id]
                save_json(ARKTIPS_FILE, data)
        else:
            data = load_page(pf)
            data = [e for e in data if str(e.get("id","")) != item_id]
            save_json(pf, data)
        arktips_remove(item_id)
    else:
        data = load_json(ANN_FILE)
        if isinstance(data, list):
            data = [e for e in data if str(e.get("id","")) != item_id]
            save_json(ANN_FILE, data)

    return jsonify({"ok": True})


@app.route("/pull", methods=["POST"])
def pull():
    tab    = request.args.get("tab", "arktips")
    ok, msg = git_pull()
    safe   = urllib.parse.quote(msg)
    t      = "success" if ok else "warning"
    return redirect(f"/?message={safe}&type={t}&tab={tab}")


@app.route("/push", methods=["POST"])
def push():
    tab    = request.args.get("tab", "arktips")
    ok, msg = git_push()
    safe   = urllib.parse.quote(msg)
    t      = "success" if ok else "warning"
    return redirect(f"/?message={safe}&type={t}&tab={tab}")


if __name__ == "__main__":
    url = "http://127.0.0.1:5000"
    def open_browser():
        webbrowser.open(url)
    threading.Timer(1.2, open_browser).start()
    app.run(host="127.0.0.1", port=5000, debug=False)