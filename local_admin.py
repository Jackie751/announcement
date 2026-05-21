from flask import Flask, request, redirect, render_template_string
import json
from pathlib import Path
from datetime import datetime, date
import shutil
import webbrowser
import threading
import subprocess
import urllib.parse

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent

JSON_FILES = {
    "announcements": BASE_DIR / "announcements.json",
    "arktips":       BASE_DIR / "arktips.json",
}

BACKUP_DIR = BASE_DIR / "json_backups"


def get_json_file(file_key="announcements"):
    return JSON_FILES.get(file_key, JSON_FILES["announcements"])


def ensure_files(json_file=None):
    BACKUP_DIR.mkdir(exist_ok=True)
    if json_file is None:
        json_file = get_json_file()
    if not json_file.exists():
        json_file.parent.mkdir(parents=True, exist_ok=True)
        json_file.write_text("[]", encoding="utf-8")


def backup_json(json_file=None):
    if json_file is None:
        json_file = get_json_file()
    ensure_files(json_file)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"{json_file.stem}_backup_{timestamp}.json"
    shutil.copy2(json_file, backup_path)
    return backup_path


def load_data(json_file=None):
    if json_file is None:
        json_file = get_json_file()

    ensure_files(json_file)
    text = json_file.read_text(encoding="utf-8").strip()

    if not text:
        return [], "list"

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        return [], f"broken: {e}"

    if isinstance(raw, list):
        return raw, "list"

    if isinstance(raw, dict) and isinstance(raw.get("announcements"), list):
        return raw["announcements"], "dict_announcements"

    return [], "unknown"


def save_data(items, json_file=None):
    if json_file is None:
        json_file = get_json_file()

    ensure_files(json_file)
    backup_json(json_file)

    _, mode = load_data(json_file)

    if mode == "dict_announcements":
        output = {"announcements": items}
    else:
        output = items

    json_file.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def parse_bool(value):
    return value in ("on", "true", "True", "1", 1, True)


def parse_pin_order(value):
    value = str(value or "").strip()
    if not value:
        return 999999

    try:
        num = int(value)
        return num if num > 0 else 999999
    except ValueError:
        return 999999


def run_cmd(args, cwd=None):
    result = subprocess.run(
        args,
        cwd=str(cwd or BASE_DIR),
        capture_output=True,
        text=True,
        shell=False
    )
    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    return result.returncode == 0, output


def ensure_gitignore():
    gitignore = BASE_DIR / ".gitignore"
    line = "json_backups/"

    if gitignore.exists():
        text = gitignore.read_text(encoding="utf-8", errors="ignore")
        lines = [x.strip() for x in text.splitlines()]

        if line not in lines:
            with gitignore.open("a", encoding="utf-8") as f:
                if text and not text.endswith("\n"):
                    f.write("\n")
                f.write(line + "\n")
    else:
        gitignore.write_text(line + "\n", encoding="utf-8")


def get_current_branch():
    """
    获取当前分支名。

    修复点：
    之前使用 git rev-parse --abbrev-ref HEAD。
    如果仓库处于 detached HEAD 状态，它会返回 HEAD，
    然后程序会执行 git push origin HEAD，导致 Git 报：

    The destination you provided is not a full refname

    现在优先使用 git branch --show-current。
    如果取不到分支，就默认推 main。
    """
    ok, out = run_cmd(["git", "branch", "--show-current"])
    branch = out.strip() if ok and out else ""

    if branch and branch != "HEAD":
        return branch

    return "main"


def git_push_current_head_to_branch(branch):
    """
    明确把当前 HEAD 推送到远程分支。

    等价于：
    git push origin HEAD:refs/heads/main

    这样即使本地处于 detached HEAD，也不会再出现 full refname 错误。
    """
    return run_cmd(["git", "push", "origin", f"HEAD:refs/heads/{branch}"])


def commit_local_changes_if_any():
    ensure_gitignore()

    ok, out = run_cmd(["git", "add", "-A"])
    if not ok:
        return False, "git add 失败：\n" + out

    msg = f"Update {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    ok_commit, commit_out = run_cmd(["git", "commit", "-m", msg])

    text = (commit_out or "").lower()

    if ok_commit:
        return True, "已提交本地修改。"

    nothing_cases = [
        "nothing to commit",
        "no changes added to commit",
        "nothing added to commit"
    ]

    if any(x in text for x in nothing_cases):
        return True, "没有新的本地修改需要提交。"

    return False, "git commit 失败：\n" + commit_out


def run_git_pull_only():
    ok, out = run_cmd(["git", "rev-parse", "--is-inside-work-tree"])
    if not ok or "true" not in out.lower():
        return False, f"当前目录不是 Git 仓库：{BASE_DIR}"

    branch = get_current_branch()

    ok_commit, commit_msg = commit_local_changes_if_any()
    if not ok_commit:
        return False, commit_msg

    ok_pull, pull_out = run_cmd(["git", "pull", "--rebase", "origin", branch])
    if not ok_pull:
        return False, "拉取失败：\n" + pull_out

    return True, f"已拉取远程 origin/{branch}。"


def run_git_pull_then_push():
    ok, out = run_cmd(["git", "rev-parse", "--is-inside-work-tree"])
    if not ok or "true" not in out.lower():
        return False, f"当前目录不是 Git 仓库：{BASE_DIR}"

    branch = get_current_branch()

    ok_commit, commit_msg = commit_local_changes_if_any()
    if not ok_commit:
        return False, commit_msg

    ok_pull, pull_out = run_cmd(["git", "pull", "--rebase", "origin", branch])
    if not ok_pull:
        return False, "git pull --rebase 失败：\n" + pull_out

    ok_push, push_out = git_push_current_head_to_branch(branch)
    if not ok_push:
        return False, "git push 失败：\n" + push_out

    return True, f"已完成：提交 → 拉取 origin/{branch} → 推送。"


def run_git_push():
    try:
        ok, out = run_cmd(["git", "rev-parse", "--is-inside-work-tree"])
        if not ok or "true" not in out.lower():
            return False, "当前目录不是 Git 仓库。"

        ensure_gitignore()

        branch = get_current_branch()

        ok, out = run_cmd(["git", "add", "."])
        if not ok:
            return False, "git add 失败：\n" + out

        commit_msg = f"Update {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        ok_commit, commit_out = run_cmd(["git", "commit", "-m", commit_msg])

        commit_text = (commit_out or "").lower()

        if not ok_commit:
            nothing_cases = [
                "nothing to commit",
                "no changes added to commit",
                "nothing added to commit"
            ]
            if not any(x in commit_text for x in nothing_cases):
                return False, "git commit 失败：\n" + commit_out

        ok_pull, pull_out = run_cmd(["git", "pull", "--rebase", "origin", branch])
        if not ok_pull:
            return False, "git pull --rebase 失败：\n" + pull_out

        ok_push, push_out = git_push_current_head_to_branch(branch)
        if not ok_push:
            return False, "git push 失败：\n" + push_out

        return True, f"已推送到 origin/{branch}。"

    except FileNotFoundError:
        return False, "找不到 git，请安装 Git 并加入 PATH。"
    except Exception as e:
        return False, str(e)


HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>JSON 编辑器</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Microsoft YaHei",Arial,sans-serif;background:#0f0f1e;color:#f0f0ff;padding:28px 20px;}
.container{max-width:1000px;margin:auto;}
.topbar{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid #2a2a4a;}
.topbar h1{font-size:1.3em;color:#8be9ff;letter-spacing:.04em;}
.file-path{font-family:Consolas,monospace;font-size:12px;color:#6688aa;margin-top:3px;}
.switch-btn{display:inline-flex;align-items:center;gap:6px;padding:7px 16px;border-radius:999px;background:rgba(180,126,255,.15);border:1px solid rgba(180,126,255,.4);color:#d0a0ff;font-size:13px;font-weight:600;text-decoration:none;transition:all .2s;}
.switch-btn:hover{background:rgba(180,126,255,.28);border-color:#b47eff;color:#fff;}
.msg{padding:11px 16px;border-radius:10px;margin-bottom:16px;font-size:14px;white-space:pre-wrap;}
.success{background:#0d2b18;border:1px solid #1e6b36;color:#7fffaa;}
.warning{background:#2b1e08;border:1px solid #7a4a00;color:#ffd166;}
.panel{background:#181830;border:1px solid #2a2a4a;border-radius:16px;padding:22px;margin-bottom:20px;}
.panel h2{font-size:1em;font-weight:700;color:#c0d8ff;margin-bottom:16px;letter-spacing:.06em;text-transform:uppercase;}
label{font-size:13px;color:#8899bb;display:block;margin-bottom:4px;margin-top:12px;}
label:first-child{margin-top:0;}
input,select,textarea{width:100%;padding:10px 13px;border-radius:10px;border:1px solid #333355;background:#0a0a1a;color:#f0f0ff;font-size:14px;transition:border-color .2s;}
input:focus,select:focus,textarea:focus{outline:none;border-color:#6688ff;}
textarea{min-height:160px;line-height:1.65;resize:vertical;}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:14px;}
.hint{font-size:11px;color:#556688;margin-top:3px;}
.pin-row{display:flex;align-items:center;gap:16px;margin-top:14px;padding:12px 14px;background:#12122a;border-radius:10px;border:1px solid #2a2a4a;}
.pin-row label{margin:0;color:#ffd166;font-size:13px;font-weight:600;display:flex;align-items:center;gap:6px;cursor:pointer;}
.pin-row input[type=checkbox]{width:16px;height:16px;accent-color:#ffd166;cursor:pointer;}
.pin-row input[type=number]{width:100px;padding:6px 10px;font-size:13px;}
.btn{display:inline-flex;align-items:center;gap:5px;padding:9px 20px;border-radius:999px;border:none;font-weight:700;font-size:14px;cursor:pointer;transition:opacity .18s;}
.btn:hover{opacity:.82;}
.btn-save{background:#7ddcff;color:#0a0a1a;}
.btn-cancel{background:#444466;color:#ccc;}
.btn-del{background:#ff5f7e;color:#fff;}
.btn-edit{background:#d6b2ff;color:#0a0a1a;}
.btn-pull{background:#a6ff8f;color:#0a0a1a;}
.btn-push{background:#7ddcff;color:#0a0a1a;}
.btn-push2{background:#ffd166;color:#0a0a1a;}
.btn-gap{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:6px;}
.card-list{display:flex;flex-direction:column;gap:12px;}
.card{background:#181830;border:1px solid #2a2a4a;border-radius:14px;padding:16px 18px;transition:border-color .2s;}
.card:hover{border-color:#445588;}
.card-header{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:8px;}
.card-title{font-size:14px;font-weight:700;color:#c8e0ff;line-height:1.4;flex:1;}
.card-badges{display:flex;gap:6px;flex-wrap:wrap;align-items:center;flex-shrink:0;}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11px;font-weight:600;}
.badge-pin{border:1px solid rgba(255,210,90,.5);background:rgba(255,210,90,.1);color:#ffd76a;}
.badge-cat{border:1px solid rgba(139,233,255,.35);background:rgba(139,233,255,.08);color:#8be9ff;}
.badge-ch{border:1px solid rgba(180,126,255,.35);background:rgba(180,126,255,.08);color:#c09aff;}
.card-meta{font-size:12px;color:#556688;margin-bottom:8px;}
.card-text{font-size:13px;color:#aabbcc;line-height:1.6;white-space:pre-wrap;word-break:break-word;max-height:80px;overflow:hidden;}
.card-text.expanded{max-height:none;}
.expand-btn{font-size:11px;color:#7799ff;cursor:pointer;margin-top:4px;display:inline-block;}
.card-img{margin-top:8px;}
.card-img img{max-width:120px;max-height:80px;border-radius:8px;object-fit:cover;border:1px solid #333355;}
.card-actions{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;}
.section-title{font-size:1.1em;font-weight:700;color:#c0d8ff;margin:24px 0 12px;letter-spacing:.04em;}
.count-badge{display:inline-block;margin-left:8px;padding:1px 10px;border-radius:999px;background:#1e2a3a;border:1px solid #334466;color:#7799aa;font-size:12px;font-weight:400;}
@media(max-width:700px){.row2{grid-template-columns:1fr;}}
</style>
</head>
<body>
<div class="container">

<div class="topbar">
  <div>
    <h1>📋 JSON 编辑器</h1>
    <div class="file-path">{{ json_path }}</div>
  </div>
  <a class="switch-btn" href="/?file={{ other_file }}">🔄 {{ other_label }}</a>
</div>

{% if message %}
<div class="msg {{ message_type }}">{{ message }}</div>
{% endif %}

<div class="panel">
<h2>{% if editing %}✏️ 编辑条目{% else %}➕ 新增条目{% endif %}</h2>

{% if file_key == "arktips" %}
<form method="post" action="{% if editing %}/update/{{ edit_index }}?file=arktips{% else %}/add?file=arktips{% endif %}">
  <div class="row2">
    <div>
      <label>频道 channel</label>
      <input name="channel" placeholder="例如：@ARKTIPS" value="{{ edit_item.get('channel', '') }}">
    </div>
    <div>
      <label>日期 date</label>
      <input name="date" value="{{ edit_item.get('date', today) }}">
    </div>
  </div>
  <label>正文 text</label>
  <textarea name="text" placeholder="消息正文...">{{ edit_item.get('text', '') }}</textarea>
  <div class="row2">
    <div>
      <label>图片链接 image（可留空）</label>
      <input name="image" placeholder="https://..." value="{{ edit_item.get('image', '') }}">
    </div>
    <div>
      <label>分类 category</label>
      <select name="category">
        {% for cat in categories %}
        <option value="{{ cat }}" {% if edit_item.get('category', '活动') == cat %}selected{% endif %}>{{ cat }}</option>
        {% endfor %}
      </select>
    </div>
  </div>
  <div class="pin-row">
    <label>
      <input type="checkbox" name="important" {% if edit_item.get('important') %}checked{% endif %}>
      📌 置顶
    </label>
    <div>
      <input type="number" name="pinOrder" min="1" placeholder="顺序（1最前）"
        value="{{ edit_item.get('pinOrder', '') if edit_item.get('pinOrder', 999999) != 999999 else '' }}">
    </div>
  </div>
  <div class="btn-gap" style="margin-top:18px;">
    <button class="btn btn-save" type="submit">{% if editing %}💾 保存修改{% else %}✅ 保存{% endif %}</button>
    {% if editing %}
    <a href="/?file=arktips" style="text-decoration:none;">
      <button class="btn btn-cancel" type="button">取消</button>
    </a>
    {% endif %}
  </div>
</form>

{% else %}
<form method="post" action="{% if editing %}/update/{{ edit_index }}?file=announcements{% else %}/add?file=announcements{% endif %}">
  <div class="row2">
    <div>
      <label>标题 title</label>
      <input name="title" placeholder="例如：站点更新公告" value="{{ edit_item.get('title', '') }}" required>
    </div>
    <div>
      <label>日期 date</label>
      <input name="date" value="{{ edit_item.get('date', today) }}" required>
    </div>
  </div>
  <div class="row2">
    <div>
      <label>分类 category</label>
      <select name="category">
        {% for cat in categories %}
        <option value="{{ cat }}" {% if edit_item.get('category', '') == cat %}selected{% endif %}>{{ cat }}</option>
        {% endfor %}
      </select>
    </div>
    <div>
      <label>图片链接（可留空）</label>
      <input name="image" placeholder="https://..." value="{{ edit_item.get('image', '') }}">
    </div>
  </div>
  <label>正文 content</label>
  <textarea name="content" placeholder="可多行，支持换行和编号。" required>{{ edit_item.get('content', '') }}</textarea>
  <div class="pin-row">
    <label>
      <input type="checkbox" name="important" {% if edit_item.get('important') %}checked{% endif %}>
      📌 置顶
    </label>
    <div>
      <input type="number" name="pinOrder" min="1" placeholder="顺序（1最前）"
        value="{{ edit_item.get('pinOrder', '') if edit_item.get('pinOrder', 999999) != 999999 else '' }}">
      <div class="hint">数字越小越靠前，不填则按时间排</div>
    </div>
  </div>
  <div class="btn-gap" style="margin-top:18px;">
    <button class="btn btn-save" type="submit">{% if editing %}💾 保存修改{% else %}✅ 保存公告{% endif %}</button>
    {% if editing %}
    <a href="/?file=announcements" style="text-decoration:none;">
      <button class="btn btn-cancel" type="button">取消</button>
    </a>
    {% endif %}
  </div>
</form>
{% endif %}
</div>

<div class="panel">
<h2>🔄 同步 Git</h2>
<div class="btn-gap">
  <form method="post" action="/pull" style="margin:0;">
    <button class="btn btn-pull" type="submit">① Pull 拉取</button>
  </form>
  <form method="post" action="/pull-push" style="margin:0;">
    <button class="btn btn-push" type="submit">② Pull → Push（推荐）</button>
  </form>
  <form method="post" action="/push" style="margin:0;">
    <button class="btn btn-push2" type="submit">普通 Push</button>
  </form>
</div>
<div class="hint" style="margin-top:10px;">推荐用②，执行：git add -A → commit → pull --rebase → push</div>
</div>

<div class="section-title">
  已有条目
  <span class="count-badge">{{ items|length }} 条</span>
</div>

<div class="card-list">
{% for item in items %}
<div class="card">
  <div class="card-header">
    <div class="card-title">
      {% if file_key == "arktips" %}
        {{ loop.index }}. {{ (item.get("text","") or "")[:60] }}{% if (item.get("text","") or "")|length > 60 %}…{% endif %}
      {% else %}
        {{ loop.index }}. {{ item.get("title", "无标题") }}
      {% endif %}
    </div>
    <div class="card-badges">
      {% if item.get("important") %}<span class="badge badge-pin">📌 #{{ item.get("pinOrder","?") }}</span>{% endif %}
      {% if item.get("category") %}<span class="badge badge-cat">{{ item.get("category") }}</span>{% endif %}
      {% if item.get("channel") %}<span class="badge badge-ch">{{ item.get("channel") }}</span>{% endif %}
    </div>
  </div>
  <div class="card-meta">
    {% if file_key == "arktips" %}{{ item.get("time", item.get("date","")) }}
    {% else %}{{ item.get("date","") }}{% endif %}
  </div>
  {% if file_key == "arktips" %}
    <div class="card-text" id="ct-{{ loop.index0 }}">{{ (item.get("text",""))[:200] }}{% if (item.get("text",""))|length > 200 %}…{% endif %}</div>
    {% if (item.get("text",""))|length > 200 %}
    <span class="expand-btn" onclick="toggleText({{ loop.index0 }}, this)">展开 ▾</span>
    {% endif %}
  {% else %}
    <div class="card-text">{{ (item.get("content","") or "")[:200] }}{% if (item.get("content","") or "")|length > 200 %}…{% endif %}</div>
  {% endif %}
  {% if item.get("image") %}
  <div class="card-img"><img src="{{ item.get('image') }}" alt="img" onerror="this.style.display='none'"></div>
  {% endif %}
  <div class="card-actions">
    <form method="post" action="/edit/{{ loop.index0 }}" style="margin:0;">
      <input type="hidden" name="file" value="{{ file_key }}">
      <button class="btn btn-edit" type="submit">✏️ 编辑</button>
    </form>
    <form method="post" action="/delete/{{ loop.index0 }}" style="margin:0;"
          onsubmit="return confirm('确定删除这条吗？')">
      <input type="hidden" name="file" value="{{ file_key }}">
      <button class="btn btn-del" type="submit">🗑 删除</button>
    </form>
  </div>
</div>
{% else %}
<div style="color:#556688;text-align:center;padding:30px;">暂无条目</div>
{% endfor %}
</div>

</div>
<script>
function toggleText(idx, btn) {
  const el = document.getElementById('ct-' + idx);
  if (!el) return;
  if (el.classList.contains('expanded')) {
    el.classList.remove('expanded');
    btn.textContent = '展开 ▾';
  } else {
    el.classList.add('expanded');
    btn.textContent = '收起 ▴';
  }
}
</script>
</body>
</html>
"""


def render_page(message="", message_type="success", edit_index=None, file_key="announcements"):
    json_file = get_json_file(file_key)
    items, mode = load_data(json_file)
    editing = edit_index is not None and 0 <= edit_index < len(items)

    if file_key == "arktips":
        default_item = {
            "channel": "",
            "date": str(date.today()),
            "text": "",
            "image": "",
            "category": "活动",
            "important": False,
            "pinOrder": 999999
        }
    else:
        default_item = {
            "title": "",
            "date": str(date.today()),
            "category": "更新",
            "content": "",
            "image": "",
            "important": False,
            "pinOrder": 999999
        }

    edit_item = items[edit_index] if editing else default_item

    return render_template_string(
        HTML,
        items=items,
        mode=mode,
        today=str(date.today()),
        json_path=str(json_file),
        message=message,
        message_type=message_type,
        categories=["重要", "更新", "维护", "活动", "资源更新", "其他"],
        editing=editing,
        edit_index=edit_index if editing else "",
        edit_item=edit_item,
        file_key=file_key,
        other_file="arktips" if file_key == "announcements" else "announcements",
        other_label="切换到 arktips.json" if file_key == "announcements" else "切换到 announcements.json",
    )


@app.route("/")
def index():
    message = request.args.get("message", "")
    message_type = request.args.get("type", "success")
    file_key = request.args.get("file", "announcements")
    return render_page(message=message, message_type=message_type, file_key=file_key)


@app.route("/add", methods=["POST"])
def add():
    file_key = request.args.get("file", "announcements")
    json_file = get_json_file(file_key)
    items, mode = load_data(json_file)

    if mode.startswith("broken"):
        msg = urllib.parse.quote(f"JSON 格式损坏，无法安全新增 {json_file.name}。")
        return redirect(f"/?message={msg}&type=warning&file={file_key}")

    if file_key == "arktips":
        item = {
            "id":        int(datetime.now().timestamp()),
            "channel":   request.form.get("channel", "").strip(),
            "date":      request.form.get("date", "").strip(),
            "time":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "text":      request.form.get("text", "").strip(),
            "image":     request.form.get("image", "").strip(),
            "images":    [request.form.get("image", "").strip()] if request.form.get("image", "").strip() else [],
            "category":  request.form.get("category", "活动").strip(),
            "important": parse_bool(request.form.get("important")),
            "pinOrder":  parse_pin_order(request.form.get("pinOrder")),
        }
    else:
        item = {
            "title":     request.form.get("title", "").strip(),
            "date":      request.form.get("date", "").strip(),
            "category":  request.form.get("category", "").strip(),
            "content":   request.form.get("content", "").strip(),
            "image":     request.form.get("image", "").strip(),
            "important": parse_bool(request.form.get("important")),
            "pinOrder":  parse_pin_order(request.form.get("pinOrder")),
        }

    items.insert(0, item)
    save_data(items, json_file)

    msg = urllib.parse.quote(f"已保存到 {json_file.name}。")
    return redirect(f"/?message={msg}&type=success&file={file_key}")


@app.route("/edit/<int:index>", methods=["GET", "POST"])
def edit(index):
    file_key = request.form.get("file") or request.args.get("file", "announcements")
    json_file = get_json_file(file_key)
    items, mode = load_data(json_file)

    if not (0 <= index < len(items)):
        msg = urllib.parse.quote("编辑失败：索引不存在。")
        return redirect(f"/?message={msg}&type=warning&file={file_key}")

    return render_page(edit_index=index, file_key=file_key)


@app.route("/update/<int:index>", methods=["POST"])
def update(index):
    file_key = request.form.get("file") or request.args.get("file", "announcements")
    json_file = get_json_file(file_key)
    items, mode = load_data(json_file)

    if mode.startswith("broken"):
        msg = urllib.parse.quote(f"JSON 格式损坏，无法安全修改 {json_file.name}。")
        return redirect(f"/?message={msg}&type=warning&file={file_key}")

    if not (0 <= index < len(items)):
        msg = urllib.parse.quote("修改失败：索引不存在。")
        return redirect(f"/?message={msg}&type=warning&file={file_key}")

    old_item = items[index]

    if file_key == "arktips":
        img = request.form.get("image", "").strip()
        items[index] = {
            **old_item,
            "channel":   request.form.get("channel", "").strip(),
            "date":      request.form.get("date", "").strip(),
            "text":      request.form.get("text", "").strip(),
            "image":     img,
            "images":    [img] if img else old_item.get("images", []),
            "category":  request.form.get("category", "活动").strip(),
            "important": parse_bool(request.form.get("important")),
            "pinOrder":  parse_pin_order(request.form.get("pinOrder")),
        }
    else:
        items[index] = {
            **old_item,
            "title":     request.form.get("title", "").strip(),
            "date":      request.form.get("date", "").strip(),
            "category":  request.form.get("category", "").strip(),
            "content":   request.form.get("content", "").strip(),
            "image":     request.form.get("image", "").strip(),
            "important": parse_bool(request.form.get("important")),
            "pinOrder":  parse_pin_order(request.form.get("pinOrder")),
        }

    save_data(items, json_file)

    msg = urllib.parse.quote("已修改并保存。")
    return redirect(f"/?message={msg}&type=success&file={file_key}")


@app.route("/delete/<int:index>", methods=["POST"])
def delete(index):
    file_key = request.form.get("file") or request.args.get("file", "announcements")
    json_file = get_json_file(file_key)
    items, mode = load_data(json_file)

    if mode.startswith("broken"):
        msg = urllib.parse.quote(f"JSON 格式损坏，无法安全删除 {json_file.name}。")
        return redirect(f"/?message={msg}&type=warning&file={file_key}")

    if 0 <= index < len(items):
        items.pop(index)
        save_data(items, json_file)

        msg = urllib.parse.quote("已删除，并已自动备份。")
        return redirect(f"/?message={msg}&type=success&file={file_key}")

    msg = urllib.parse.quote("删除失败：索引不存在。")
    return redirect(f"/?message={msg}&type=warning&file={file_key}")


@app.route("/pull", methods=["POST"])
def pull_remote():
    file_key = request.args.get("file", "announcements")
    ok, msg = run_git_pull_only()
    safe_msg = urllib.parse.quote(msg)

    if ok:
        return redirect(f"/?message={safe_msg}&type=success&file={file_key}")

    return redirect(f"/?message={urllib.parse.quote('Git 拉取失败：' + msg)}&type=warning&file={file_key}")


@app.route("/pull-push", methods=["POST"])
def pull_then_push():
    file_key = request.args.get("file", "announcements")
    ok, msg = run_git_pull_then_push()
    safe_msg = urllib.parse.quote(msg)

    if ok:
        return redirect(f"/?message={safe_msg}&type=success&file={file_key}")

    return redirect(f"/?message={urllib.parse.quote('Git 操作失败：' + msg)}&type=warning&file={file_key}")


@app.route("/push", methods=["POST"])
def push():
    file_key = request.args.get("file", "announcements")
    ok, msg = run_git_push()
    safe_msg = urllib.parse.quote(msg)

    if ok:
        return redirect(f"/?message={safe_msg}&type=success&file={file_key}")

    return redirect(f"/?message={urllib.parse.quote('Git 推送失败：' + msg)}&type=warning&file={file_key}")


if __name__ == "__main__":
    url = "http://127.0.0.1:5000"

    def open_browser():
        webbrowser.open(url)

    threading.Timer(1.2, open_browser).start()
    app.run(host="127.0.0.1", port=5000, debug=False)