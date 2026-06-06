# local.py — 本地管理工具 v3
# 不使用 Jinja2，用占位符替换，彻底避免花括号冲突

from flask import Flask, request, redirect, Response
import json
from pathlib import Path
from datetime import datetime, date
import shutil
import webbrowser
import threading
import subprocess
import urllib.parse
import re
import os

# VPS 模式：export VPS_MODE=1 启动即为 VPS 模式，否则为本地模式
VPS_MODE = os.environ.get("VPS_MODE", "0") == "1"

# 前端密码验证（VPS 模式可选，需要 auth.py 在同目录）
try:
    from auth import init_auth
    _auth_available = True
except ImportError:
    _auth_available = False

app = Flask(__name__)

BASE_DIR   = Path(__file__).resolve().parent
BACKUP_DIR = BASE_DIR / "json_backups"

ANN_FILE     = BASE_DIR / "announcements.json"
ARKTIPS_FILE = BASE_DIR / "arktips.json"
PAGE_PREFIX  = "arktips-"
PAGE_SIZE    = 100


# ──────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────
def backup(path: Path):
    BACKUP_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(path, BACKUP_DIR / f"{path.stem}_backup_{ts}.json")

def load_json(path: Path):
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

def h(s) -> str:
    """HTML 转义"""
    return str(s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"','&quot;')


# ──────────────────────────────────────────────────────────────
# 分页文件管理
# ──────────────────────────────────────────────────────────────
def get_page_files() -> list[Path]:
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

def find_item_page(item_id) -> tuple:
    for page_file in get_page_files():
        items = load_page(page_file)
        for i, item in enumerate(items):
            if str(item.get("id")) == str(item_id):
                return page_file, i
    return None, -1


# ──────────────────────────────────────────────────────────────
# arktips.json 置顶管理
# ──────────────────────────────────────────────────────────────
def arktips_upsert(item: dict):
    data = load_json(ARKTIPS_FILE)
    if not isinstance(data, list):
        data = []
    data = [e for e in data if str(e.get("id")) != str(item.get("id"))]
    data.insert(0, item)
    save_json(ARKTIPS_FILE, data)

def arktips_remove(item_id):
    data = load_json(ARKTIPS_FILE)
    if not isinstance(data, list):
        return
    data = [e for e in data if str(e.get("id")) != str(item_id)]
    save_json(ARKTIPS_FILE, data)

def cleanup_expired_pins():
    data = load_json(ARKTIPS_FILE)
    if not isinstance(data, list):
        return
    today = datetime.now().date()
    removed_ids = []
    cleaned = []
    for item in data:
        expiry = item.get("pinExpiry", "")
        if expiry:
            try:
                exp_date = datetime.strptime(expiry, "%Y-%m-%d").date()
                if exp_date < today:
                    removed_ids.append(str(item.get("id", "")))
                    print(f"[CLEANUP] 过期置顶移除: id={item.get('id')} expiry={expiry}")
                    continue
            except ValueError:
                pass
        cleaned.append(item)
    if removed_ids:
        save_json(ARKTIPS_FILE, cleaned)
        for pf in get_page_files():
            items = load_page(pf)
            changed = False
            for item in items:
                if str(item.get("id", "")) in removed_ids and item.get("important"):
                    item["important"] = False
                    changed = True
            if changed:
                save_json(pf, items)
        print(f"[CLEANUP] 清理完成，移除 {len(removed_ids)} 条")
    else:
        print(f"[CLEANUP] 无过期置顶")


# ──────────────────────────────────────────────────────────────
# Git 操作
# ──────────────────────────────────────────────────────────────
def run_cmd(args, cwd=None):
    result = subprocess.run(args, cwd=str(cwd or BASE_DIR), capture_output=True, text=True, shell=False)
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
    run_cmd(["git", "add", "."])
    msg = f"Update {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    run_cmd(["git", "commit", "-m", msg])
    ok2, out2 = run_cmd(["git", "pull", "--rebase", "origin", branch])
    if not ok2:
        return False, "git pull 失败：" + out2
    ok3, out3 = run_cmd(["git", "push", "origin", f"HEAD:refs/heads/{branch}"])
    if not ok3:
        return False, "git push 失败：" + out3
    return True, f"已推送到 origin/{branch}"

def git_push_and_destroy():
    """VPS 模式：push 成功后延迟 1.5 秒销毁整个仓库目录并退出进程"""
    ensure_gitignore()
    branch = get_current_branch()
    run_cmd(["git", "add", "."])
    msg = f"Update {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    run_cmd(["git", "commit", "-m", msg])
    ok_pull, out_pull = run_cmd(["git", "pull", "--rebase", "origin", branch])
    if not ok_pull:
        return False, "git pull 失败：" + out_pull
    ok_push, out_push = run_cmd(["git", "push", "origin", f"HEAD:refs/heads/{branch}"])
    if not ok_push:
        return False, "git push 失败：" + out_push
    def self_destruct():
        import time
        time.sleep(1.5)
        print("[VPS] Push 成功，正在销毁仓库目录...")
        shutil.rmtree(BASE_DIR, ignore_errors=True)
        os._exit(0)
    threading.Thread(target=self_destruct, daemon=True).start()
    return True, f"✅ 已推送到 origin/{branch}，仓库将在 1 秒后自动销毁，请关闭此页面。"

def git_pull():
    branch = get_current_branch()
    ensure_gitignore()
    run_cmd(["git", "add", "."])
    run_cmd(["git", "commit", "-m", f"local save {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"])
    ok, out = run_cmd(["git", "pull", "--rebase", "origin", branch])
    return ok, out


# ──────────────────────────────────────────────────────────────
# HTML 生成（纯字符串拼接，不用 Jinja2）
# ──────────────────────────────────────────────────────────────
HTML_HEAD = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>本地管理 · Local Manager</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Noto+Sans+SC:wght@300;400;500&family=Noto+Serif+SC:wght@300;400&family=Share+Tech+Mono&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{min-height:100%;background:#05050f;color:#dde;font-family:'Noto Sans SC',sans-serif;font-size:15px;}
canvas#particles{position:fixed;inset:0;pointer-events:none;z-index:0;}
.topbar{position:sticky;top:0;z-index:100;background:rgba(6,4,20,.88);border-bottom:1px solid rgba(180,126,255,.18);padding:10px 24px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;backdrop-filter:blur(18px);}
.topbar h1{font-family:'Orbitron',monospace;font-size:.9em;color:#b47eff;flex-shrink:0;letter-spacing:.1em;}
.tab-btn{padding:5px 16px;border-radius:999px;border:1px solid rgba(180,126,255,.3);background:transparent;color:#b47eff;cursor:pointer;font-size:13px;transition:all .2s;}
.tab-btn.active,.tab-btn:hover{background:rgba(180,126,255,.15);border-color:#b47eff;}
.git-btn{padding:5px 16px;border-radius:999px;border:1px solid rgba(0,229,255,.3);background:transparent;color:#00e5ff;cursor:pointer;font-size:13px;transition:all .2s;}
.git-btn:hover{background:rgba(0,229,255,.1);}
.git-btn.push{border-color:rgba(74,222,128,.3);color:#4ade80;}
.git-btn.push:hover{background:rgba(74,222,128,.1);}
.msg{padding:9px 24px;font-size:13px;border-bottom:1px solid rgba(255,255,255,.06);position:relative;z-index:1;}
.msg.success{color:#4ade80;background:rgba(74,222,128,.07);}
.msg.warning{color:#fbbf24;background:rgba(251,191,36,.07);}
.msg.error{color:#f87171;background:rgba(248,113,113,.07);}
.layout{display:flex;gap:0;position:relative;z-index:1;}
.main-col{flex:1;min-width:0;padding:20px 20px 20px 24px;}
.side-col{width:260px;flex-shrink:0;padding:20px 20px 20px 0;}
.add-form{background:rgba(8,5,28,.55);border:1px solid rgba(180,126,255,.18);border-radius:14px;padding:20px 22px;margin-bottom:22px;backdrop-filter:blur(12px);}
.add-form h2{font-family:'Orbitron',monospace;font-size:.78em;color:#b47eff;margin-bottom:14px;letter-spacing:.12em;}
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;}
.form-full{grid-column:1/-1;}
.form-row{display:flex;flex-direction:column;gap:4px;}
label{font-size:10px;color:rgba(180,200,255,.4);letter-spacing:.1em;text-transform:uppercase;font-family:'Share Tech Mono',monospace;}
input[type=text],input[type=date],textarea,select{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);border-radius:7px;color:#eef;padding:8px 11px;font-size:13px;font-family:'Noto Sans SC',sans-serif;width:100%;outline:none;transition:border-color .2s;}
input:focus,textarea:focus,select:focus{border-color:rgba(180,126,255,.55);background:rgba(180,126,255,.04);}
select option{background:#0e0e1e;}
textarea{resize:vertical;min-height:72px;}
.checkbox-row{display:flex;align-items:center;gap:9px;padding:5px 0;}
.checkbox-row input[type=checkbox]{width:16px;height:16px;accent-color:#b47eff;}
.checkbox-row label{font-size:13px;color:#ccb;cursor:pointer;text-transform:none;letter-spacing:0;}
.btn{padding:8px 20px;border-radius:7px;border:none;cursor:pointer;font-size:13px;font-family:'Noto Sans SC',sans-serif;transition:all .2s;}
.btn-primary{background:linear-gradient(135deg,#b47eff,#7c4fff);color:#fff;font-weight:600;}
.btn-primary:hover{transform:translateY(-1px);}
.btn-sm{padding:4px 11px;font-size:12px;border-radius:5px;}
.btn-edit{background:rgba(0,229,255,.1);color:#00e5ff;border:1px solid rgba(0,229,255,.22);}
.btn-edit:hover{background:rgba(0,229,255,.2);}
.btn-delete{background:rgba(248,113,113,.1);color:#f87171;border:1px solid rgba(248,113,113,.22);}
.btn-delete:hover{background:rgba(248,113,113,.2);}
.btn-pin{background:rgba(255,210,90,.1);color:#ffd76a;border:1px solid rgba(255,210,90,.22);}
.btn-pin:hover{background:rgba(255,210,90,.2);}
.btn-unpin{background:rgba(180,126,255,.1);color:#b47eff;border:1px solid rgba(180,126,255,.22);}
.btn-unpin:hover{background:rgba(180,126,255,.2);}
.item-card{background:rgba(8,5,28,.45);border:1px solid rgba(255,255,255,.08);border-radius:12px;padding:14px 16px;margin-bottom:9px;transition:border-color .2s;backdrop-filter:blur(8px);}
.item-card:hover{border-color:rgba(180,126,255,.25);}
.item-card.is-pinned{border-color:rgba(255,210,90,.3);background:rgba(255,210,90,.04);}
.item-card.selected{outline:2px solid rgba(180,126,255,.6);}
.item-top{display:flex;align-items:flex-start;gap:8px;margin-bottom:6px;}
.item-num{font-family:'Share Tech Mono',monospace;font-size:11px;color:rgba(180,200,255,.22);flex-shrink:0;padding-top:2px;min-width:30px;}
.item-title{font-family:'Noto Serif SC',serif;font-size:14px;color:#eef0ff;flex:1;line-height:1.6;word-break:break-word;font-weight:300;}
.item-badges{display:flex;gap:4px;flex-wrap:wrap;flex-shrink:0;}
.badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:999px;font-size:10px;border:1px solid currentColor;font-family:'Share Tech Mono',monospace;}
.badge-pin{color:#ffd76a;}.badge-cat{color:#b47eff;}.badge-ch{color:#6ee7b7;}
.item-meta{font-family:'Share Tech Mono',monospace;font-size:11px;color:rgba(180,200,255,.22);margin-bottom:7px;}
.item-content{font-size:13px;color:rgba(200,210,255,.5);background:rgba(255,255,255,.03);border-left:2px solid rgba(180,126,255,.18);padding:7px 11px;border-radius:0 6px 6px 0;margin-bottom:9px;white-space:pre-wrap;max-height:72px;overflow:hidden;}
.item-content.expanded{max-height:none;}
.expand-btn{font-family:'Share Tech Mono',monospace;font-size:10px;color:rgba(180,200,255,.25);cursor:pointer;background:none;border:none;padding:2px 4px;}
.expand-btn:hover{color:#b47eff;}
.item-images{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px;}
.item-img{width:64px;height:64px;object-fit:cover;border-radius:7px;border:1px solid rgba(180,126,255,.15);}
.item-actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;}
.page-label{font-family:'Share Tech Mono',monospace;font-size:10px;color:rgba(180,126,255,.2);padding:8px 0 10px;letter-spacing:.1em;text-transform:uppercase;}
.side-panel{background:rgba(8,5,28,.55);border:1px solid rgba(180,126,255,.15);border-radius:14px;padding:18px 16px;backdrop-filter:blur(12px);position:sticky;top:60px;max-height:calc(100vh - 80px);overflow-y:auto;overflow-x:hidden;scrollbar-width:thin;scrollbar-color:rgba(180,126,255,.25) transparent;}
.side-panel h3{font-family:'Orbitron',monospace;font-size:.72em;color:rgba(180,126,255,.6);letter-spacing:.12em;margin-bottom:14px;}
.side-section{margin-bottom:16px;}
.side-section-title{font-family:'Share Tech Mono',monospace;font-size:10px;color:rgba(180,200,255,.25);letter-spacing:.1em;text-transform:uppercase;margin-bottom:7px;}
.quick-btn{display:flex;align-items:center;gap:8px;width:100%;padding:7px 11px;border-radius:7px;border:1px solid rgba(255,255,255,.07);background:rgba(255,255,255,.03);color:rgba(200,215,255,.6);cursor:pointer;font-size:13px;font-family:'Noto Sans SC',sans-serif;transition:all .2s;margin-bottom:5px;text-align:left;}
.quick-btn:hover{background:rgba(180,126,255,.1);border-color:rgba(180,126,255,.25);color:#eef;}
.qicon{font-size:14px;flex-shrink:0;}
.selected-id{font-family:'Share Tech Mono',monospace;font-size:11px;color:rgba(180,200,255,.3);padding:5px 0;min-height:18px;}
.expiry-mini{display:flex;flex-direction:column;gap:5px;}
.expiry-mini input{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);border-radius:7px;color:#eef;padding:7px 10px;font-size:13px;width:100%;outline:none;}
.search-input-side{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);border-radius:7px;color:#eef;padding:7px 10px;font-size:13px;width:100%;outline:none;transition:border-color .2s;}
.search-input-side:focus{border-color:rgba(180,126,255,.5);}
.list-count-bar{font-family:'Share Tech Mono',monospace;font-size:11px;color:rgba(180,200,255,.25);}
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.82);z-index:500;overflow-y:auto;backdrop-filter:blur(4px);}
.modal-overlay.show{display:flex;align-items:flex-start;justify-content:center;padding:36px 16px;}
.modal{background:rgba(8,5,28,.97);border:1px solid rgba(180,126,255,.3);border-radius:16px;padding:26px;width:100%;max-width:640px;box-shadow:0 20px 60px rgba(0,0,0,.5);}
.modal h2{font-family:'Orbitron',monospace;font-size:.88em;color:#b47eff;margin-bottom:18px;letter-spacing:.1em;}
.modal-actions{display:flex;gap:10px;margin-top:18px;flex-wrap:wrap;}
.btn-cancel{background:rgba(255,255,255,.06);color:rgba(180,200,255,.45);border:1px solid rgba(255,255,255,.1);}
.btn-cancel:hover{background:rgba(255,255,255,.1);color:#dde;}
.sentinel{height:48px;display:flex;align-items:center;justify-content:center;font-family:'Share Tech Mono',monospace;color:rgba(180,200,255,.18);font-size:11px;}
.sentinel.loading::after{content:'';width:17px;height:17px;border:2px solid rgba(180,126,255,.2);border-top-color:#b47eff;border-radius:50%;animation:spin .8s linear infinite;display:inline-block;}
@keyframes spin{to{transform:rotate(360deg)}}
#float-nav{position:fixed;bottom:24px;right:24px;z-index:999;display:flex;flex-direction:column;align-items:center;gap:8px;}
.fnav-btn{width:40px;height:40px;border-radius:50%;border:1px solid rgba(180,126,255,.25);background:rgba(8,5,28,.85);backdrop-filter:blur(10px);color:rgba(180,200,255,.7);font-size:16px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .2s;box-shadow:0 4px 14px rgba(0,0,0,.4);}
.fnav-btn:hover{background:rgba(180,126,255,.2);border-color:#b47eff;color:#fff;}
.fnav-jump{display:flex;align-items:center;gap:4px;background:rgba(8,5,28,.85);backdrop-filter:blur(10px);border:1px solid rgba(180,126,255,.25);border-radius:20px;padding:4px 8px;}
.fnav-jump input{width:64px;height:32px;padding:4px 8px;border-radius:8px;border:1px solid rgba(180,126,255,.2);background:rgba(255,255,255,.05);color:#eef;font-size:14px;text-align:center;outline:none;}
.fnav-jump input:focus{border-color:rgba(180,126,255,.5);}
.fnav-jump button{width:26px;height:26px;border-radius:50%;border:none;background:rgba(180,126,255,.2);color:#d0b0ff;font-size:12px;cursor:pointer;transition:all .2s;}
.fnav-jump button:hover{background:rgba(180,126,255,.4);color:#fff;}
</style>
</head>
<body>
<canvas id="particles"></canvas>
"""

HTML_JS = """
<div class="modal-overlay" id="modalOverlay">
  <div class="modal">
    <h2>✏️ 编辑条目</h2>
    <form method="post" id="editForm" action="/update">
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
var currentTab   = '__TAB__';
var allItems     = [];
var rendered     = 0;
var isLoading    = false;
var selectedIdx  = -1;
var searchTerm   = '';
var filteredItems = [];
var BATCH = 30;
var topRemoved   = 0;
var RECYCLE_KEEP = 60;

function switchTab(tab) { window.location.href = '/?tab=' + tab; }

function jumpToCard() {
  var input = document.getElementById('jumpNum');
  var n = parseInt(input.value);
  if (!n || n < 1) return;

  while (rendered < Math.min(n, filteredItems.length)) {
    renderBatch();
  }

  var cards = document.querySelectorAll('#itemList .item-card');
  var target = cards[n - 1];
  if (target) {
    selectCard(n - 1);
    target.scrollIntoView({behavior:'smooth', block:'center'});
    if (input) input.blur();
  }
}

document.addEventListener('DOMContentLoaded', function() {
  var jn = document.getElementById('jumpNum');
  if (jn) {
    jn.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') { e.preventDefault(); jumpToCard(); }
    });
  }
});

function loadData() {
  fetch('/api/items?tab=' + currentTab).then(function(r) { return r.json(); }).then(function(d) {
    allItems = d.items || [];
    filteredItems = allItems;
    rendered = 0;
    topRemoved = 0;
    selectedIdx = -1;
    document.getElementById('selectedId').textContent = '— 点击卡片选中 —';
    document.getElementById('itemList').innerHTML = '';
    document.getElementById('listCountBar').textContent = '共 ' + allItems.length + ' 条';
    renderBatch();
  });
}

function onSearch() {
  searchTerm = document.getElementById('searchInput').value.toLowerCase().trim();
  filteredItems = searchTerm ? allItems.filter(function(item) {
    var s = (item.title||'') + (item.text||'') + (item.content||'') + (item.channel||'') + String(item.id||'');
    return s.toLowerCase().indexOf(searchTerm) >= 0;
  }) : allItems;
  rendered = 0; topRemoved = 0; selectedIdx = -1;
  document.getElementById('itemList').innerHTML = '';
  document.getElementById('listCountBar').textContent = searchTerm
    ? '找到 ' + filteredItems.length + ' / ' + allItems.length + ' 条'
    : '共 ' + allItems.length + ' 条';
  renderBatch();
}

function clearSearch() { document.getElementById('searchInput').value = ''; onSearch(); }

function renderBatch() {
  if (rendered >= filteredItems.length) {
    var s = document.getElementById('sentinel');
    s.textContent = filteredItems.length ? '— 已全部加载 —' : '— 暂无数据 —';
    s.classList.remove('loading'); return;
  }
  var batch = filteredItems.slice(rendered, rendered + BATCH);
  var list  = document.getElementById('itemList');
  var html  = '';
  var lastPage = '';
  for (var i = 0; i < batch.length; i++) {
    var item = batch[i];
    var idx  = rendered + i;
    if (item._page && item._page !== lastPage) {
      html += '<div class="page-label">── ' + esc(item._page) + ' ──</div>';
      lastPage = item._page;
    }
    html += renderCard(item, idx);
  }
  list.insertAdjacentHTML('beforeend', html);
  rendered += batch.length;
  recycleTop();
}

function recycleTop() {
  var list = document.getElementById('itemList');
  var cards = list.querySelectorAll('.item-card');
  if (cards.length <= RECYCLE_KEEP) return;
  var removeCount = cards.length - RECYCLE_KEEP;
  for (var i = 0; i < removeCount; i++) {
    var card = list.querySelector('.item-card');
    if (!card) break;
    // 只回收已滚出视口上方的
    if (card.getBoundingClientRect().bottom > 0) break;
    var cardIdx = parseInt(card.id.replace('card-', ''));
    if (cardIdx === selectedIdx) selectedIdx = -1;
    // 移除前面可能的 page-label
    var first = list.firstChild;
    if (first && first !== card && first.classList && first.classList.contains('page-label')) {
      list.removeChild(first);
    }
    list.removeChild(card);
    topRemoved++;
  }
}

function renderCard(item, idx) {
  var title   = esc(item.title || (item.text||'').slice(0,60) || '无标题');
  var cat     = esc(item.category || '');
  var ch      = esc(item.channel || '');
  var date    = esc(item.date || item.time || '');
  var content = esc(item.content || item.text || '');
  var pinned  = item.important === true || item.important === 'true' || item.important === 1;
  var imgs    = Array.isArray(item.images) ? item.images.filter(Boolean) : (item.image ? [item.image] : []);
  var vids    = Array.isArray(item.videos) ? item.videos.filter(Boolean) : [];
  var badges  = (pinned ? '<span class="badge badge-pin">📌</span>' : '') +
                (cat ? '<span class="badge badge-cat">' + cat + '</span>' : '') +
                (ch  ? '<span class="badge badge-ch">'  + ch  + '</span>' : '');
  var imgHtml = imgs.slice(0,3).map(function(u) {
    return '<img class="item-img" src="' + esc(u) + '">';
  }).join('');
  var vidHtml = vids.slice(0,2).map(function(u) {
    return '<video src="' + esc(u) + '" controls preload="metadata" style="max-width:100%;max-height:120px;border-radius:7px;border:1px solid rgba(180,126,255,.15);margin-top:4px;display:block;"></video>';
  }).join('');
  var contentHtml = content
    ? '<div class="item-content" id="ct-' + idx + '">' + content + '</div>' +
      '<button class="expand-btn" onclick="event.stopPropagation();toggleContent(' + idx + ')">展开 ▾</button>'
    : '';
  return '<div class="item-card' + (pinned?' is-pinned':'') + '" id="card-' + idx + '" onclick="selectCard(' + idx + ')" style="cursor:pointer">' +
    '<div class="item-top">' +
    '<span class="item-num">' + String(idx+1).padStart(3,'0') + '</span>' +
    '<span class="item-title">' + title + '</span>' +
    '<div class="item-badges">' + badges + '</div>' +
    '</div>' +
    '<div class="item-meta">' + date + (item.id ? ' &nbsp;·&nbsp; id:' + esc(String(item.id)) : '') + '</div>' +
    contentHtml +
    (imgHtml ? '<div class="item-images">' + imgHtml + '</div>' : '') +
    (vidHtml ? '<div style="margin-top:6px">' + vidHtml + '</div>' : '') +
    '</div>';
}

function esc(s) {
  var d = document.createElement('div');
  d.textContent = String(s || '');
  return d.innerHTML;
}

function toggleContent(idx) {
  var el = document.getElementById('ct-' + idx);
  var btn = el && el.nextElementSibling;
  if (!el) return;
  el.classList.toggle('expanded');
  if (btn) btn.textContent = el.classList.contains('expanded') ? '收起 ▴' : '展开 ▾';
}

function selectCard(idx) {
  if (selectedIdx >= 0) {
    var prev = document.getElementById('card-' + selectedIdx);
    if (prev) prev.classList.remove('selected');
  }
  selectedIdx = idx;
  var el = document.getElementById('card-' + idx);
  if (el) el.classList.add('selected');
  var item = filteredItems[idx];
  if (item) {
    var title = (item.title || (item.text||'').slice(0,20) || '无标题').slice(0,24);
    document.getElementById('selectedId').textContent = '#' + (idx+1) + ' · ' + title;
    if (item.pinExpiry) document.getElementById('quickExpiryDate').value = item.pinExpiry;
    var po = document.getElementById('quickPinOrderInput');
    if (po) po.value = (item.pinOrder && item.pinOrder !== 999999) ? item.pinOrder : '';
    initCatButtons(item.category || '');
  }
}

function scrollToSelected() {
  if (selectedIdx < 0) { alert('请先点击选中一个条目'); return; }
  var el = document.getElementById('card-' + selectedIdx);
  if (el) el.scrollIntoView({behavior:'smooth', block:'center'});
}

function getSelected() {
  if (selectedIdx < 0) { alert('请先点击选中一个条目'); return null; }
  return filteredItems[selectedIdx];
}

function quickEdit() { if (selectedIdx < 0) { alert('请先选中条目'); return; } openEdit(selectedIdx); }

function postJson(url, data, cb) {
  fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)})
    .then(function(r) { return r.json(); }).then(cb);
}

function quickTogglePin(pin) {
  var item = getSelected(); if (!item) return;
  postJson('/api/toggle-pin', {item_id:item.id, tab:currentTab, pin:pin}, function(d) {
    if (d.ok) { alert(pin ? '✅ 已置顶' : '✅ 已取消置顶'); loadData(); } else alert('失败：' + d.msg);
  });
}

function quickCycleCategory() {
  var item = getSelected(); if (!item) return;
  var cycle = ['活动','资源更新','预告资讯','社区周边','其他'];
  var next  = cycle[(cycle.indexOf(item.category||'活动') + 1) % cycle.length];
  postJson('/api/set-field', {item_id:item.id, tab:currentTab, field:'category', value:next}, function(d) {
    if (d.ok) { alert('✅ 分类：' + next); loadData(); } else alert('失败：' + d.msg);
  });
}

function quickSetPinOrder() {
  var item = getSelected(); if (!item) return;
  var val = parseInt(document.getElementById('quickPinOrderInput').value);
  if (!val || val < 1) { alert('请输入有效编号'); return; }
  postJson('/api/set-field', {item_id:item.id, tab:currentTab, field:'pinOrder', value:val}, function(d) {
    if (d.ok) { alert('✅ 置顶编号：' + val); loadData(); } else alert('失败：' + d.msg);
  });
}

function quickClearPinOrder() {
  var item = getSelected(); if (!item) return;
  postJson('/api/set-field', {item_id:item.id, tab:currentTab, field:'pinOrder', value:999999}, function(d) {
    if (d.ok) { alert('✅ 已清除置顶编号'); loadData(); } else alert('失败：' + d.msg);
  });
}

function quickSetCategory(cat) {
  var item = getSelected(); if (!item) return;
  postJson('/api/set-field', {item_id:item.id, tab:currentTab, field:'category', value:cat}, function(d) {
    if (d.ok) { initCatButtons(cat); loadData(); } else alert('失败：' + d.msg);
  });
}

function initCatButtons(activeCat) {
  var cats = currentTab === 'arktips'
    ? ['活动','资源更新','预告资讯','社区周边','其他']
    : ['重要','更新','维护','活动','预告资讯','社区周边','其他'];
  var container = document.getElementById('quickCatButtons');
  if (!container) return;
  container.innerHTML = cats.map(function(c) {
    var isActive = c === (activeCat || '');
    var style = isActive
      ? 'background:rgba(180,126,255,.35);border-color:#b47eff;color:#fff;'
      : '';
    return '<button data-cat="' + c + '" style="padding:3px 9px;border-radius:999px;border:1px solid rgba(180,126,255,.25);background:rgba(180,126,255,.08);color:rgba(200,200,255,.7);cursor:pointer;font-size:11px;font-family:sans-serif;transition:all .15s;' + style + '">' + c + '</button>';
  }).join('');
  Array.prototype.forEach.call(container.querySelectorAll('button[data-cat]'), function(btn) {
    btn.addEventListener('click', function() { quickSetCategory(this.getAttribute('data-cat')); });
  });
}

function quickExtractTitle() {
  var item = getSelected(); if (!item) return;
  var raw  = (item.content || item.text || '').trim();
  var lines = raw.split('\\n');
  var first = '';
  for (var i = 0; i < lines.length; i++) { if (lines[i].trim()) { first = lines[i].trim(); break; } }
  if (!first) { alert('内容为空'); return; }
  var title = first.slice(0,80);
  postJson('/api/set-field', {item_id:item.id, tab:currentTab, field:'title', value:title}, function(d) {
    if (d.ok) { alert('✅ 标题：' + title.slice(0,30)); loadData(); } else alert('失败：' + d.msg);
  });
}

function quickSetExpiry() {
  var item = getSelected(); if (!item) return;
  var val  = document.getElementById('quickExpiryDate').value;
  if (!val) { alert('请先选择日期'); return; }
  postJson('/api/set-field', {item_id:item.id, tab:currentTab, field:'pinExpiry', value:val}, function(d) {
    if (d.ok) { alert('✅ 截止：' + val); loadData(); } else alert('失败：' + d.msg);
  });
}

function quickClearExpiry() {
  var item = getSelected(); if (!item) return;
  postJson('/api/set-field', {item_id:item.id, tab:currentTab, field:'pinExpiry', value:''}, function(d) {
    if (d.ok) { alert('✅ 已清除截止日期'); loadData(); } else alert('失败：' + d.msg);
  });
}

function quickDelete() {
  var item = getSelected(); if (!item) return;
  if (!confirm('确认删除？')) return;
  postJson('/api/delete', {item_id:item.id, tab:currentTab}, function(d) {
    if (d.ok) { selectedIdx = -1; loadData(); } else alert('失败：' + d.msg);
  });
}

function openEdit(idx) {
  var item = filteredItems[idx]; if (!item) return;
  document.getElementById('editTab').value      = currentTab;
  document.getElementById('editItemId').value   = item.id;
  document.getElementById('editPageFile').value = item._page_file || '';
  var fields = '';
  if (currentTab === 'arktips') {
    var imgs = Array.isArray(item.images) ? item.images.join('\\n') : (item.image||'');
    var vids = Array.isArray(item.videos) ? item.videos.join('\\n') : '';
    var cats = ['活动','资源更新','预告资讯','社区周边','其他'].map(function(c) {
      return '<option value="' + c + '"' + (item.category===c?' selected':'') + '>' + c + '</option>';
    }).join('');
    fields = '<div class="form-row"><label>频道</label><input type="text" name="channel" value="' + esc(item.channel||'') + '"></div>' +
      '<div class="form-row"><label>日期</label><input type="date" name="date" value="' + esc(item.date||'') + '"></div>' +
      '<div class="form-row form-full"><label>标题</label><input type="text" name="title" value="' + esc(item.title||'') + '"></div>' +
      '<div class="form-row form-full"><label>文本内容</label><textarea name="text" rows="4">' + esc(item.text||item.content||'') + '</textarea></div>' +
      '<div class="form-row form-full"><label>图片链接（每行一个）</label><textarea name="images" rows="3">' + esc(imgs) + '</textarea></div>' +
      '<div class="form-row form-full"><label>视频链接（每行一个）</label><textarea name="videos" rows="3" placeholder="https://...">' + esc(vids) + '</textarea></div>' +
      '<div class="form-row"><label>分类</label><select name="category">' + cats + '</select></div>' +
      '<div class="form-row"><label>置顶顺序</label><input type="text" name="pinOrder" value="' + esc(item.pinOrder===999999?'':item.pinOrder) + '"></div>' +
      '<div class="form-row"><label>截止日期</label><input type="date" name="pinExpiry" value="' + esc(item.pinExpiry||'') + '"></div>' +
      '<div class="form-row form-full checkbox-row"><input type="checkbox" name="important" id="imp_edit"' + ((item.important===true||item.important==='true'||item.important===1)?' checked':'') + '>' +
      '<label for="imp_edit" style="text-transform:none;letter-spacing:0;font-size:13px;color:#ccb;">📌 置顶（保存后自动同步 arktips.json）</label></div>';
  } else {
    var cats2 = ['重要','更新','维护','活动','预告资讯','社区周边','其他'].map(function(c) {
      return '<option value="' + c + '"' + (item.category===c?' selected':'') + '>' + c + '</option>';
    }).join('');
    fields = '<div class="form-row form-full"><label>标题</label><input type="text" name="title" value="' + esc(item.title||'') + '"></div>' +
      '<div class="form-row"><label>日期</label><input type="date" name="date" value="' + esc(item.date||'') + '"></div>' +
      '<div class="form-row"><label>分类</label><select name="category">' + cats2 + '</select></div>' +
      '<div class="form-row form-full"><label>内容</label><textarea name="content" rows="4">' + esc(item.content||'') + '</textarea></div>' +
      '<div class="form-row form-full"><label>图片链接</label><input type="text" name="image" value="' + esc(item.image||'') + '"></div>' +
      '<div class="form-row"><label>置顶顺序</label><input type="text" name="pinOrder" value="' + esc(item.pinOrder===999999?'':item.pinOrder) + '"></div>' +
      '<div class="form-row"><label>截止日期</label><input type="date" name="pinExpiry" value="' + esc(item.pinExpiry||'') + '"></div>' +
      '<div class="form-row form-full checkbox-row"><input type="checkbox" name="important" id="imp_edit2"' + ((item.important===true||item.important==='true'||item.important===1)?' checked':'') + '>' +
      '<label for="imp_edit2" style="text-transform:none;letter-spacing:0;font-size:13px;color:#ccb;">📌 置顶</label></div>';
  }
  document.getElementById('editFields').innerHTML = fields;
  document.getElementById('modalOverlay').classList.add('show');
}

function closeModal() { document.getElementById('modalOverlay').classList.remove('show'); }

var observer = new IntersectionObserver(function(entries) {
  if (entries[0].isIntersecting && !isLoading) {
    isLoading = true;
    document.getElementById('sentinel').classList.add('loading');
    setTimeout(function() { renderBatch(); isLoading = false; document.getElementById('sentinel').classList.remove('loading'); }, 80);
  }
}, {rootMargin:'200px'});
observer.observe(document.getElementById('sentinel'));

// 滚动时自动回收顶部卡片
window.addEventListener('scroll', function() {
  if (rendered >= filteredItems.length) recycleTop();
}, {passive: true});

document.getElementById('modalOverlay').addEventListener('click', function(e) {
  if (e.target === this) closeModal();
});

// ── 快捷键 ──
document.addEventListener('keydown', function(event) {
  var key = event.key.toLowerCase();
  var active = document.activeElement;
  var tag = active && active.tagName ? active.tagName.toLowerCase() : '';
  var isTyping = tag === 'input' || tag === 'textarea' || tag === 'select';

  if (event.key === 'Escape') { closeModal(); return; }

  if (event.ctrlKey && key === 'p') {
    event.preventDefault();
    var saveBtn = document.querySelector('.add-form form .btn-primary');
    if (saveBtn) saveBtn.closest('form').requestSubmit();
    return;
  }

  if (isTyping) return;

  if (event.ctrlKey && key === 'a') {
    event.preventDefault();
    window.scrollTo({top:0, behavior:'smooth'});
  }
  if (event.ctrlKey && key === 'd') {
    event.preventDefault();
    window.scrollTo({top:document.body.scrollHeight, behavior:'smooth'});
  }
  if (event.ctrlKey && key === 'g') {
    event.preventDefault();
    var jn = document.getElementById('jumpNum');
    if (jn) { jn.focus(); jn.select(); }
  }
  if (event.ctrlKey && key === 'e') {
    event.preventDefault();
    quickEdit();
  }
  if (key === 'c') {
    event.preventDefault();
    quickCycleCategory();
  }
  if (event.ctrlKey && key === 'm') {
    event.preventDefault();
    if (confirm('拉取远程？')) {
      fetch('/pull?tab=' + currentTab, {method:'POST'}).then(function() { location.reload(); });
    }
  }
  if (event.ctrlKey && key === 'i') {
    event.preventDefault();
    if (confirm('推送到 GitHub？')) {
      fetch('/push?tab=' + currentTab, {method:'POST'}).then(function() { location.reload(); });
    }
  }
});

// ── 粒子背景 ──
(function() {
  var canvas = document.getElementById('particles');
  var ctx    = canvas.getContext('2d');
  function resize() { canvas.width = window.innerWidth; canvas.height = window.innerHeight; }
  resize(); window.addEventListener('resize', resize);
  var dots = [];
  for (var i = 0; i < 70; i++) {
    dots.push({
      x:Math.random()*window.innerWidth, y:Math.random()*window.innerHeight,
      r:Math.random()*1.3+.3, vx:(Math.random()-.5)*.22, vy:(Math.random()-.5)*.22,
      alpha:Math.random()*.45+.12,
      hue:Math.random()<.5?'255,110,180':Math.random()<.5?'0,229,255':'180,120,255'
    });
  }
  (function draw() {
    ctx.clearRect(0,0,canvas.width,canvas.height);
    for (var i = 0; i < dots.length; i++) {
      var d = dots[i];
      d.x+=d.vx; d.y+=d.vy;
      if(d.x<0)d.x=canvas.width; if(d.x>canvas.width)d.x=0;
      if(d.y<0)d.y=canvas.height; if(d.y>canvas.height)d.y=0;
      ctx.beginPath(); ctx.arc(d.x,d.y,d.r,0,Math.PI*2);
      ctx.fillStyle='rgba('+d.hue+','+d.alpha+')'; ctx.fill();
    }
    requestAnimationFrame(draw);
  })();
})();

loadData();
initCatButtons('');
</script>
</body>
</html>
"""


def build_form_html(tab: str, today: str) -> str:
    if tab == "arktips":
        return f"""
      <form method="post" action="/add?tab=arktips">
        <div class="form-grid">
          <div class="form-row"><label>频道</label><input type="text" name="channel" placeholder="@ARKTIPS"></div>
          <div class="form-row"><label>日期</label><input type="date" name="date" value="{today}"></div>
          <div class="form-row form-full"><label>标题</label><input type="text" name="title" placeholder="留空则取文本前50字"></div>
          <div class="form-row form-full"><label>文本内容</label><textarea name="text" rows="3" placeholder="消息正文"></textarea></div>
          <div class="form-row form-full"><label>图片链接（每行一个）</label><textarea name="images" rows="2" placeholder="https://..."></textarea></div>
          <div class="form-row"><label>分类</label>
            <select name="category">
              <option value="活动">活动</option>
              <option value="资源更新">资源更新</option>
              <option value="预告资讯">预告资讯</option>
              <option value="社区周边">社区周边</option>
              <option value="其他">其他</option>
            </select>
          </div>
          <div class="form-row"><label>置顶顺序</label><input type="text" name="pinOrder" placeholder="留空=不置顶"></div>
          <div class="form-row"><label>截止日期</label><input type="date" name="pinExpiry"></div>
          <div class="form-row form-full checkbox-row">
            <input type="checkbox" name="important" id="imp_new">
            <label for="imp_new" style="text-transform:none;letter-spacing:0;font-size:13px;color:#ccb;">📌 置顶（自动同步到 arktips.json）</label>
          </div>
        </div>
        <div style="margin-top:14px"><button class="btn btn-primary" type="submit">保存</button></div>
      </form>"""
    else:
        return f"""
      <form method="post" action="/add?tab=announcements">
        <div class="form-grid">
          <div class="form-row form-full"><label>标题</label><input type="text" name="title" placeholder="公告标题"></div>
          <div class="form-row"><label>日期</label><input type="date" name="date" value="{today}"></div>
          <div class="form-row"><label>分类</label>
            <select name="category">
              <option value="重要">重要</option>
              <option value="更新">更新</option>
              <option value="维护">维护</option>
              <option value="活动">活动</option>
              <option value="预告资讯">预告资讯</option>
              <option value="社区周边">社区周边</option>
              <option value="其他">其他</option>
            </select>
          </div>
          <div class="form-row form-full"><label>内容</label><textarea name="content" rows="3"></textarea></div>
          <div class="form-row form-full"><label>图片链接</label><input type="text" name="image" placeholder="https://..."></div>
          <div class="form-row"><label>置顶顺序</label><input type="text" name="pinOrder" placeholder="留空=不置顶"></div>
          <div class="form-row"><label>截止日期</label><input type="date" name="pinExpiry"></div>
          <div class="form-row form-full checkbox-row">
            <input type="checkbox" name="important" id="imp_new2">
            <label for="imp_new2" style="text-transform:none;letter-spacing:0;font-size:13px;color:#ccb;">📌 置顶</label>
          </div>
        </div>
        <div style="margin-top:14px"><button class="btn btn-primary" type="submit">保存</button></div>
      </form>"""


def render_page(tab="arktips", message="", message_type="success"):
    today = datetime.now().strftime("%Y-%m-%d")
    arktips_active = "active" if tab == "arktips" else ""
    ann_active     = "active" if tab == "announcements" else ""
    msg_html = f'<div class="msg {h(message_type)}">{h(message)}</div>' if message else ""
    form_html = build_form_html(tab, today)

    body = f"""
<div class="topbar">
  <h1>📋 Local Manager</h1>
  <span style="font-family:'Share Tech Mono',monospace;font-size:11px;color:rgba(180,200,255,.65);letter-spacing:.04em;">
    P=保存 &nbsp;E=编辑 &nbsp;C=切换分类 &nbsp;A=顶部 &nbsp;D=底部 &nbsp;G=跳转 &nbsp;M=Pull &nbsp;I=Push &nbsp;Esc=关闭
  </span>
  <button class="tab-btn {arktips_active}" onclick="switchTab('arktips')">资源区</button>
  <button class="tab-btn {ann_active}" onclick="switchTab('announcements')">公告</button>
  <form method="post" action="/pull?tab={tab}" style="display:inline" onsubmit="return confirm('拉取远程？')">
    <button class="git-btn" type="submit">⬇ Pull</button>
  </form>
  <form method="post" action="/push?tab={tab}" style="display:inline" onsubmit="return confirm('推送到 GitHub？')">
    <button class="git-btn push" type="submit">⬆ Push</button>
  </form>
</div>

{msg_html}

<div class="layout">
  <div class="main-col">
    <div class="add-form" id="addForm">
      <h2>＋ 新增条目</h2>
      {form_html}
    </div>
    <div id="itemList"></div>
    <div class="sentinel" id="sentinel"></div>
  </div>

  <div class="side-col">
    <div class="side-panel">
      <h3>⚡ 快捷操作</h3>
      <div class="side-section">
        <div class="side-section-title">导航 &nbsp;<span style="font-size:9px;opacity:.3">Ctrl+A/D</span></div>
        <div style="display:flex;gap:5px;align-items:center;margin-bottom:6px;">
          <button class="fnav-btn" style="width:34px;height:34px;flex:1;" onclick="window.scrollTo({{top:0,behavior:'smooth'}})" title="顶部">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"/></svg>
          </button>
          <button class="fnav-btn" style="width:34px;height:34px;flex:1;" onclick="window.scrollTo({{top:document.body.scrollHeight,behavior:'smooth'}})" title="底部">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
          </button>
          <button class="fnav-btn" style="width:34px;height:34px;flex:1;" onclick="scrollToSelected()" title="定位选中">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3"/></svg>
          </button>
          <button class="fnav-btn" style="width:34px;height:34px;flex:1;" onclick="loadData();window.scrollTo({{top:0,behavior:'smooth'}})" title="刷新">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
          </button>
        </div>
        <div class="fnav-jump" style="width:100%;padding:5px 10px;border-radius:10px;">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="rgba(180,126,255,.5)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          <input type="number" id="jumpNum" min="1" placeholder="跳到第 N 条" title="跳到第N条" style="flex:1;min-width:0;">
          <button onclick="jumpToCard()" title="跳转" style="width:24px;height:24px;">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
          </button>
        </div>
      </div>
      <div class="side-section">
        <div class="side-section-title">搜索</div>
        <div style="display:flex;gap:5px;align-items:center;">
          <input class="search-input-side" type="text" id="searchInput" placeholder="🔍 标题/内容/频道/ID" oninput="onSearch()">
          <button style="background:none;border:none;color:rgba(180,200,255,.3);cursor:pointer;font-size:15px;" onclick="clearSearch()">✕</button>
        </div>
        <div style="margin-top:4px;"><span class="list-count-bar" id="listCountBar"></span></div>
      </div>
      <div class="side-section">
        <div class="side-section-title">选中条目</div>
        <div class="selected-id" id="selectedId">— 点击卡片选中 —</div>
      </div>
      <div class="side-section">
        <div class="side-section-title">操作</div>
        <button class="quick-btn" onclick="quickEdit()"><span class="qicon">✏️</span> 编辑选中</button>
        <button class="quick-btn" onclick="quickExtractTitle()"><span class="qicon">📝</span> 提取标题</button>
        <button class="quick-btn" onclick="quickTogglePin(true)"><span class="qicon">📌</span> 设为置顶</button>
        <button class="quick-btn" onclick="quickTogglePin(false)"><span class="qicon">🔓</span> 取消置顶</button>
        <button class="quick-btn" onclick="quickDelete()"><span class="qicon">🗑️</span> 删除选中</button>
      </div>
      <div class="side-section">
        <div class="side-section-title">快捷置顶编号</div>
        <div style="display:flex;gap:5px;align-items:center;margin-bottom:5px;">
          <input type="number" id="quickPinOrderInput" min="1" placeholder="编号" style="background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);border-radius:7px;color:#eef;padding:7px 10px;font-size:13px;width:100%;outline:none;">
          <button class="btn btn-sm btn-pin" onclick="quickSetPinOrder()" style="white-space:nowrap">设置</button>
        </div>
        <button class="btn btn-sm btn-unpin" style="width:100%" onclick="quickClearPinOrder()">清除编号</button>
      </div>
      <div class="side-section">
        <div class="side-section-title">快捷分类</div>
        <div id="quickCatButtons" style="display:flex;flex-wrap:wrap;gap:4px;"></div>
      </div>
      <div class="side-section">
        <div class="side-section-title">快捷截止日期</div>
        <div class="expiry-mini">
          <input type="date" id="quickExpiryDate">
          <button class="btn btn-sm btn-pin" onclick="quickSetExpiry()">设置截止时间</button>
          <button class="btn btn-sm btn-unpin" onclick="quickClearExpiry()">清除截止时间</button>
        </div>
      </div>

    </div>
  </div>
</div>
"""
    js = HTML_JS.replace("__TAB__", tab)
    return HTML_HEAD + body + js


# ──────────────────────────────────────────────────────────────
# 路由
# ──────────────────────────────────────────────────────────────
@app.route("/")
def index():
    tab          = request.args.get("tab", "arktips")
    message      = request.args.get("message", "")
    message_type = request.args.get("type", "success")
    return Response(render_page(tab, message, message_type), mimetype="text/html")


@app.route("/api/items")
def api_items():
    from flask import jsonify
    tab   = request.args.get("tab", "arktips")
    items = []
    if tab == "arktips":
        page_files = get_page_files()
        if page_files:
            for pf in reversed(page_files):
                for item in load_page(pf):
                    item["_page"]      = pf.name
                    item["_page_file"] = str(pf)
                    items.append(item)
        else:
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
        imgs_raw  = request.form.get("images", "").strip()
        imgs      = [x.strip() for x in imgs_raw.splitlines() if x.strip()]
        raw_text  = request.form.get("text", "").strip()
        raw_title = request.form.get("title", "").strip()
        important = parse_bool(request.form.get("important"))
        item = {
            "id":        int(datetime.now().timestamp()),
            "channel":   request.form.get("channel", "").strip(),
            "date":      request.form.get("date", "").strip(),
            "time":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "title":     raw_title if raw_title else raw_text[:50],
            "text":      raw_text, "content": raw_text,
            "image":     imgs[0] if imgs else "",
            "images":    imgs, "videos": [],
            "category":  request.form.get("category", "活动").strip(),
            "important": important,
            "pinOrder":  parse_pin_order(request.form.get("pinOrder")),
            "pinExpiry": request.form.get("pinExpiry", "").strip(),
        }
        page_files = get_page_files()
        if page_files:
            last_page = page_files[-1]
            data = load_page(last_page)
            if len(data) >= PAGE_SIZE:
                next_num  = len(page_files) + 1
                last_page = BASE_DIR / f"{PAGE_PREFIX}{next_num}.json"
                data = []
            data.insert(0, item)
            save_json(last_page, data)
        else:
            data = load_json(ARKTIPS_FILE)
            if not isinstance(data, list): data = []
            data.insert(0, item)
            save_json(ARKTIPS_FILE, data)
        if important:
            arktips_upsert(item)
    else:
        important = parse_bool(request.form.get("important"))
        data = load_json(ANN_FILE)
        if not isinstance(data, list): data = []
        new_id = len(data) + 1
        item = {
            "id":        new_id,
            "title":     request.form.get("title", "").strip(),
            "date":      request.form.get("date", "").strip(),
            "category":  request.form.get("category", "").strip(),
            "content":   request.form.get("content", "").strip(),
            "image":     request.form.get("image", "").strip(),
            "important": important,
            "pinOrder":  parse_pin_order(request.form.get("pinOrder")),
            "pinExpiry": request.form.get("pinExpiry", "").strip(),
        }
        data.insert(0, item)
        # 插入后重新编号保持连续
        for i, e in enumerate(data):
            e["id"] = i + 1
        save_json(ANN_FILE, data)
    msg = urllib.parse.quote("已保存。")
    return redirect(f"/?message={msg}&type=success&tab={tab}")


@app.route("/update", methods=["POST"])
def update():
    from flask import jsonify
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
                imgs_raw  = request.form.get("images", "").strip()
                imgs      = [x.strip() for x in imgs_raw.splitlines() if x.strip()]
                vids_raw  = request.form.get("videos", "").strip()
                vids      = [x.strip() for x in vids_raw.splitlines() if x.strip()]
                raw_text  = request.form.get("text", "").strip()
                raw_title = request.form.get("title", "").strip()
                old = data[idx]
                data[idx] = {
                    **old,
                    "channel":   request.form.get("channel", "").strip(),
                    "date":      request.form.get("date", "").strip(),
                    "title":     raw_title if raw_title else raw_text[:50],
                    "text":      raw_text, "content": raw_text,
                    "image":     imgs[0] if imgs else "",
                    "images":    imgs,
                    "videos":    vids,
                    "category":  request.form.get("category", "活动").strip(),
                    "important": important,
                    "pinOrder":  parse_pin_order(request.form.get("pinOrder")),
                    "pinExpiry": request.form.get("pinExpiry", "").strip(),
                }
                save_json(pf, data)
                if important: arktips_upsert(data[idx])
                else:         arktips_remove(item_id)
    else:
        data = load_json(ANN_FILE)
        if isinstance(data, list):
            idx = next((i for i, e in enumerate(data) if str(e.get("id","")) == str(item_id)), -1)
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
    from flask import jsonify
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
        if pin: arktips_upsert(data[idx])
        else:   arktips_remove(item_id)
    else:
        data = load_json(ANN_FILE)
        if isinstance(data, list):
            for item in data:
                if str(item.get("id","")) == item_id:
                    item["important"] = pin; break
            save_json(ANN_FILE, data)
    return jsonify({"ok": True})


@app.route("/api/set-field", methods=["POST"])
def api_set_field():
    from flask import jsonify
    d       = request.get_json()
    item_id = str(d.get("item_id", ""))
    tab     = d.get("tab", "arktips")
    field   = d.get("field", "")
    value   = d.get("value", "")
    if not field:
        return jsonify({"ok": False, "msg": "field 不能为空"})
    if tab == "arktips":
        pf, idx = find_item_page(item_id)
        if pf is None:
            return jsonify({"ok": False, "msg": "找不到条目"})
        data = load_page(pf)
        data[idx][field] = value
        save_json(pf, data)
        if field in ("important","pinExpiry","title","category"):
            if data[idx].get("important") in (True,"true",1,"1","True"):
                arktips_upsert(data[idx])
            else:
                arktips_remove(item_id)
    else:
        data = load_json(ANN_FILE)
        if isinstance(data, list):
            for item in data:
                if str(item.get("id","")) == item_id:
                    item[field] = value; break
            save_json(ANN_FILE, data)
    return jsonify({"ok": True})


@app.route("/api/delete", methods=["POST"])
def api_delete():
    from flask import jsonify
    d       = request.get_json()
    item_id = str(d.get("item_id", ""))
    tab     = d.get("tab", "arktips")
    if tab == "arktips":
        pf, idx = find_item_page(item_id)
        if pf is None:
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
            # 删除后重新连续编号，保证 id 始终从 1 连续排列
            for i, e in enumerate(data):
                e["id"] = i + 1
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
    tab = request.args.get("tab", "arktips")
    if VPS_MODE:
        ok, msg = git_push_and_destroy()
    else:
        ok, msg = git_push()
    safe = urllib.parse.quote(msg)
    t    = "success" if ok else "warning"
    if VPS_MODE and ok:
        return Response(msg, mimetype="text/plain")
    return redirect(f"/?message={safe}&type={t}&tab={tab}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5000)
    args, _ = parser.parse_known_args()
    print("[STARTUP] 检查过期置顶...")
    cleanup_expired_pins()
    if _auth_available:
        init_auth(app)
    if VPS_MODE:
        print("[VPS MODE] Push 后将自动销毁仓库目录")
        host = "0.0.0.0"
    else:
        url = f"http://127.0.0.1:{args.port}"
        def open_browser():
            webbrowser.open(url)
        threading.Timer(1.2, open_browser).start()
        host = "127.0.0.1"
    app.run(host=host, port=args.port, debug=False)