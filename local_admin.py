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

# 永远以 local_admin.py 所在文件夹作为仓库目录，避免从错误目录启动导致 Git 失效
BASE_DIR = Path(__file__).resolve().parent

# 默认修改脚本同目录下的 announcements.json
# 如果你的 announcements.json 在 data/announcements.json，就改成 BASE_DIR / "data" / "announcements.json"
JSON_FILE = BASE_DIR / "announcements.json"
BACKUP_DIR = BASE_DIR / "json_backups"


def ensure_files():
    BACKUP_DIR.mkdir(exist_ok=True)

    if not JSON_FILE.exists():
        JSON_FILE.parent.mkdir(parents=True, exist_ok=True)
        JSON_FILE.write_text("[]", encoding="utf-8")


def backup_json():
    ensure_files()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"announcements_backup_{timestamp}.json"
    shutil.copy2(JSON_FILE, backup_path)
    return backup_path


def load_data():
    ensure_files()

    text = JSON_FILE.read_text(encoding="utf-8").strip()
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


def save_data(items):
    ensure_files()
    backup_json()

    _, mode = load_data()

    if mode == "dict_announcements":
        output = {"announcements": items}
    else:
        output = items

    JSON_FILE.write_text(
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
    """
    统一执行命令，返回：成功/失败 + 输出文本。
    所有 Git 命令默认固定在 local_admin.py 所在仓库目录执行。
    """
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
    """
    确保 json_backups/ 不会被提交到 GitHub。
    """
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
    ok, out = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if ok and out:
        return out.splitlines()[0].strip()
    return "main"


def commit_local_changes_if_any():
    """
    添加并提交本地修改。没有修改时不报错，继续后续 pull/push。
    """
    ensure_gitignore()

    ok, out = run_cmd(["git", "add", "-A"])
    if not ok:
        return False, "git add 失败：\n" + out

    msg = f"Update announcements {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
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
    """
    新按钮：先同步远程。
    为了避免本地未提交修改阻止 pull，这里会先自动 add/commit，再 pull --rebase。
    """
    ok, out = run_cmd(["git", "rev-parse", "--is-inside-work-tree"])
    if not ok or "true" not in out.lower():
        return False, f"当前目录不是 Git 仓库：{BASE_DIR}"

    branch = get_current_branch()

    ok_commit, commit_msg = commit_local_changes_if_any()
    if not ok_commit:
        return False, commit_msg

    ok_pull, pull_out = run_cmd(["git", "pull", "--rebase", "origin", branch])
    if not ok_pull:
        return False, (
            "拉取远程失败，可能有冲突，需要手动处理。\n\n"
            "建议在终端执行：\n"
            "git status\n"
            "git rebase --abort\n"
            f"git pull origin {branch}\n\n"
            "原始输出：\n" + pull_out
        )

    return True, f"已先提交本地改动，并成功拉取远程 origin/{branch}。"


def run_git_pull_then_push():
    """
    新按钮：先拉取远程，再推送。
    实际安全顺序：先保存/提交本地 → pull --rebase → push。
    """
    ok, out = run_cmd(["git", "rev-parse", "--is-inside-work-tree"])
    if not ok or "true" not in out.lower():
        return False, f"当前目录不是 Git 仓库：{BASE_DIR}"

    branch = get_current_branch()

    ok_commit, commit_msg = commit_local_changes_if_any()
    if not ok_commit:
        return False, commit_msg

    ok_pull, pull_out = run_cmd(["git", "pull", "--rebase", "origin", branch])
    if not ok_pull:
        return False, (
            "git pull --rebase 失败。通常是远程和本地改了同一个文件，需要手动处理冲突。\n\n"
            "建议在终端执行：\n"
            "git status\n"
            "git rebase --abort\n"
            f"git pull origin {branch}\n\n"
            "原始输出：\n" + pull_out
        )

    ok_push, push_out = run_cmd(["git", "push", "origin", branch])
    if not ok_push:
        return False, "git push 失败：\n" + push_out

    return True, f"已完成：本地提交 → 拉取远程 origin/{branch} → 推送到 GitHub。"


def run_git_push():
    """
    稳定版推送逻辑：
    1. 检查当前目录是不是 Git 仓库
    2. 自动把 json_backups/ 写入 .gitignore
    3. git add .
    4. 有变化就 commit；没变化也继续 push
    5. push 前自动 git pull --rebase origin 当前分支，解决 fetch first
    6. 再 git push origin 当前分支
    """
    try:
        ok, out = run_cmd(["git", "rev-parse", "--is-inside-work-tree"])
        if not ok or "true" not in out.lower():
            return False, "当前目录不是 Git 仓库。请把 local_admin.py 放在包含 .git 的仓库根目录运行。"

        ensure_gitignore()
        branch = get_current_branch()

        ok, out = run_cmd(["git", "add", "."])
        if not ok:
            return False, "git add 失败：\n" + out

        commit_msg = f"Update announcements {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        ok_commit, commit_out = run_cmd(["git", "commit", "-m", commit_msg])

        commit_text = commit_out.lower()
        if not ok_commit:
            # 没有新变化不是错误；可能只是本地已有 commit 还没 push。
            nothing_cases = [
                "nothing to commit",
                "no changes added to commit",
                "nothing added to commit"
            ]
            if not any(x in commit_text for x in nothing_cases):
                return False, "git commit 失败：\n" + commit_out

        # 先拉远程，解决 rejected / fetch first。
        ok_pull, pull_out = run_cmd(["git", "pull", "--rebase", "origin", branch])
        if not ok_pull:
            return False, (
                "git pull --rebase 失败。通常是远程和本地改了同一个文件，需要手动处理冲突。\n\n"
                "你可以在终端执行：\n"
                f"git status\n"
                f"git rebase --abort\n"
                f"git pull origin {branch}\n\n"
                "原始输出：\n" + pull_out
            )

        ok_push, push_out = run_cmd(["git", "push", "origin", branch])
        if not ok_push:
            return False, "git push 失败：\n" + push_out

        details = []
        if ok_commit:
            details.append("已提交本地修改")
        else:
            details.append("没有新的本地修改需要提交")
        details.append(f"已同步远程分支 origin/{branch}")
        details.append("已成功推送到 GitHub")
        return True, "；".join(details) + "。"

    except FileNotFoundError:
        return False, "找不到 git。你需要先安装 Git for Windows，或者把 git 加入 PATH。"
    except Exception as e:
        return False, str(e)


HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>公告 JSON 本地编辑器</title>
<style>
body {
    font-family: Arial, "Microsoft YaHei", sans-serif;
    background: #151527;
    color: #f5f5ff;
    padding: 30px;
}

.container {
    max-width: 980px;
    margin: auto;
}

h1 {
    color: #8be9ff;
    margin-bottom: 6px;
}

.subtitle {
    color: #aaa;
    margin-bottom: 24px;
}

.panel, .card {
    background: #22223b;
    padding: 20px;
    border-radius: 16px;
    margin-bottom: 20px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.25);
}

.row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
}

input, select, textarea {
    width: 100%;
    box-sizing: border-box;
    margin: 8px 0 15px;
    padding: 11px;
    border-radius: 10px;
    border: 1px solid #555;
    background: #11111f;
    color: white;
    font-size: 15px;
}

textarea {
    min-height: 220px;
    line-height: 1.65;
}

button {
    border: none;
    border-radius: 999px;
    padding: 10px 18px;
    background: #7ddcff;
    color: #111;
    font-weight: bold;
    cursor: pointer;
    margin-right: 8px;
}

button:hover {
    opacity: 0.88;
}

.delete {
    background: #ff5f7e;
    color: white;
}

.push {
    background: #a6ff8f;
    color: #111;
}

.edit {
    background: #d6b2ff;
    color: #111;
}

.cancel {
    background: #888;
    color: white;
}

.meta {
    color: #aaa;
    font-size: 14px;
    margin-bottom: 10px;
}

pre {
    white-space: pre-wrap;
    line-height: 1.6;
    background: #18182b;
    padding: 12px;
    border-radius: 10px;
}

.warning {
    color: #ffd166;
    background: #332a14;
    padding: 12px;
    border-radius: 10px;
    margin-bottom: 18px;
}

.success {
    color: #b6ffbd;
    background: #17351f;
    padding: 12px;
    border-radius: 10px;
    margin-bottom: 18px;
}

.path {
    color: #8be9ff;
    font-family: Consolas, monospace;
}

.pin-tag {
    display: inline-block;
    margin-left: 8px;
    padding: 2px 8px;
    border-radius: 999px;
    border: 1px solid rgba(255, 210, 90, 0.45);
    background: rgba(255, 210, 90, 0.12);
    color: #ffd76a;
    font-size: 12px;
}

.small-help {
    color: #aaa;
    font-size: 13px;
    margin-top: -8px;
    margin-bottom: 14px;
}

@media (max-width: 720px) {
    .row {
        grid-template-columns: 1fr;
    }
}
</style>
</head>
<body>
<div class="container">

<h1>公告 JSON 本地编辑器</h1>
<div class="subtitle">
当前编辑文件：
<span class="path">{{ json_path }}</span>
</div>

{% if mode.startswith("broken") %}
<div class="warning">
当前 announcements.json 格式已经损坏，程序无法读取旧内容。错误：{{ mode }}
</div>
{% elif mode == "unknown" %}
<div class="warning">
当前 JSON 结构不是数组，也不是 {"announcements": [...]}。本工具可能无法正确保存原结构。
</div>
{% endif %}

{% if message %}
<div class="{{ message_type }}">
{{ message }}
</div>
{% endif %}

<div class="panel">
<h2>{% if editing %}编辑公告{% else %}新增公告{% endif %}</h2>

<form method="post" action="{% if editing %}/update/{{ edit_index }}{% else %}/add{% endif %}">
    <div class="row">
        <div>
            <label>标题</label>
            <input name="title" placeholder="例如：站点更新公告" value="{{ edit_item.get('title', '') }}" required>
        </div>

        <div>
            <label>日期</label>
            <input name="date" value="{{ edit_item.get('date', today) }}" required>
        </div>
    </div>

    <div class="row">
        <div>
            <label>分类</label>
            <select name="category">
                {% for cat in categories %}
                <option value="{{ cat }}" {% if edit_item.get('category', '') == cat %}selected{% endif %}>{{ cat }}</option>
                {% endfor %}
            </select>
        </div>

        <div>
            <label>置顶顺序 pinOrder</label>
            <input name="pinOrder" type="number" min="1" placeholder="例如：1。数字越小越靠前。" value="{{ edit_item.get('pinOrder', '') if edit_item.get('pinOrder', 999999) != 999999 else '' }}">
            <div class="small-help">勾选置顶后生效。1 在最上面，2 在第二，普通公告继续按时间排序。</div>
        </div>
    </div>

    <label>正文</label>
    <textarea name="content" placeholder="这里可以像写文章一样写多行内容、编号、空行。" required>{{ edit_item.get('content', '') }}</textarea>

    <label>图片链接，可不填</label>
    <input name="image" placeholder="例如：https://example.com/image.jpg" value="{{ edit_item.get('image', '') }}">

    <label>
        <input type="checkbox" name="important" style="width:auto;" {% if edit_item.get('important') %}checked{% endif %}>
        置顶 / important
    </label>

    <br><br>
    <button type="submit">{% if editing %}保存修改{% else %}保存公告{% endif %}</button>

    {% if editing %}
    <a href="/" style="text-decoration:none;">
        <button class="cancel" type="button">取消编辑</button>
    </a>
    {% endif %}
</form>
</div>

<div class="panel">
<h2>同步操作</h2>

<form method="post" action="/pull" style="display:inline;">
    <button class="edit" type="submit">① 先拉取远程 Pull</button>
</form>

<form method="post" action="/pull-push" style="display:inline;">
    <button class="push" type="submit">② 拉取后推送 Pull → Push</button>
</form>

<form method="post" action="/push" style="display:inline;">
    <button type="submit">普通推送 Push</button>
</form>

<div class="meta">
推荐使用“② 拉取后推送”。它会执行：git add -A → git commit → git pull --rebase → git push，并自动忽略 json_backups/。
</div>
</div>

<h2>已有公告</h2>

{% for item in items %}
<div class="card">
    <h3>
        {{ loop.index }}. {{ item.get("title", "无标题") }}
        {% if item.get("important") %}
        <span class="pin-tag">📌 置顶 #{{ item.get("pinOrder", 999999) }}</span>
        {% endif %}
    </h3>
    <div class="meta">
        日期：{{ item.get("date", "") }} |
        分类：{{ item.get("category", "") }} |
        important：{{ item.get("important", False) }} |
        pinOrder：{{ item.get("pinOrder", 999999) }}
    </div>

    <pre>{{ item.get("content", item.get("summary", "")) }}</pre>

    {% if item.get("image") %}
    <div class="meta">图片：{{ item.get("image") }}</div>
    {% endif %}

    <form method="get" action="/edit/{{ loop.index0 }}" style="display:inline; padding:0; background:none; box-shadow:none;">
        <button class="edit" type="submit">编辑</button>
    </form>

    <form method="post" action="/delete/{{ loop.index0 }}" style="display:inline; padding:0; background:none; box-shadow:none;" onsubmit="return confirm('确定删除这条公告吗？')">
        <button class="delete" type="submit">删除</button>
    </form>
</div>
{% endfor %}

</div>
</body>
</html>
"""


def render_page(message="", message_type="success", edit_index=None):
    items, mode = load_data()

    editing = edit_index is not None and 0 <= edit_index < len(items)
    edit_item = items[edit_index] if editing else {
        "title": "",
        "date": str(date.today()),
        "category": "更新",
        "content": "",
        "image": "",
        "important": False,
        "pinOrder": ""
    }

    return render_template_string(
        HTML,
        items=items,
        mode=mode,
        today=str(date.today()),
        json_path=str(JSON_FILE),
        message=message,
        message_type=message_type,
        categories=["重要", "更新", "维护", "活动", "资源更新", "其他"],
        editing=editing,
        edit_index=edit_index if editing else "",
        edit_item=edit_item
    )


@app.route("/")
def index():
    message = request.args.get("message", "")
    message_type = request.args.get("type", "success")
    return render_page(message=message, message_type=message_type)


@app.route("/add", methods=["POST"])
def add():
    items, mode = load_data()

    if mode.startswith("broken"):
        msg = urllib.parse.quote("JSON 格式损坏，无法安全新增。请先修复 announcements.json。")
        return redirect(f"/?message={msg}&type=warning")

    item = {
        "title": request.form.get("title", "").strip(),
        "date": request.form.get("date", "").strip(),
        "category": request.form.get("category", "").strip(),
        "content": request.form.get("content", "").strip(),
        "image": request.form.get("image", "").strip(),
        "important": parse_bool(request.form.get("important")),
        "pinOrder": parse_pin_order(request.form.get("pinOrder"))
    }

    items.insert(0, item)
    save_data(items)

    msg = urllib.parse.quote("公告已保存到 announcements.json。")
    return redirect(f"/?message={msg}&type=success")


@app.route("/edit/<int:index>", methods=["GET"])
def edit(index):
    items, mode = load_data()

    if not (0 <= index < len(items)):
        msg = urllib.parse.quote("编辑失败：索引不存在。")
        return redirect(f"/?message={msg}&type=warning")

    return render_page(edit_index=index)


@app.route("/update/<int:index>", methods=["POST"])
def update(index):
    items, mode = load_data()

    if mode.startswith("broken"):
        msg = urllib.parse.quote("JSON 格式损坏，无法安全修改。请先修复 announcements.json。")
        return redirect(f"/?message={msg}&type=warning")

    if not (0 <= index < len(items)):
        msg = urllib.parse.quote("修改失败：索引不存在。")
        return redirect(f"/?message={msg}&type=warning")

    old_item = items[index]

    items[index] = {
        **old_item,
        "title": request.form.get("title", "").strip(),
        "date": request.form.get("date", "").strip(),
        "category": request.form.get("category", "").strip(),
        "content": request.form.get("content", "").strip(),
        "image": request.form.get("image", "").strip(),
        "important": parse_bool(request.form.get("important")),
        "pinOrder": parse_pin_order(request.form.get("pinOrder"))
    }

    save_data(items)

    msg = urllib.parse.quote("公告已修改并保存。")
    return redirect(f"/?message={msg}&type=success")


@app.route("/delete/<int:index>", methods=["POST"])
def delete(index):
    items, mode = load_data()

    if mode.startswith("broken"):
        msg = urllib.parse.quote("JSON 格式损坏，无法安全删除。请先修复 announcements.json。")
        return redirect(f"/?message={msg}&type=warning")

    if 0 <= index < len(items):
        items.pop(index)
        save_data(items)
        msg = urllib.parse.quote("公告已删除，并已自动备份旧 JSON。")
        return redirect(f"/?message={msg}&type=success")

    msg = urllib.parse.quote("删除失败：索引不存在。")
    return redirect(f"/?message={msg}&type=warning")


@app.route("/pull", methods=["POST"])
def pull_remote():
    ok, msg = run_git_pull_only()
    safe_msg = urllib.parse.quote(msg)
    if ok:
        return redirect(f"/?message={safe_msg}&type=success")
    return redirect(f"/?message={urllib.parse.quote('Git 拉取失败：' + msg)}&type=warning")


@app.route("/pull-push", methods=["POST"])
def pull_then_push():
    ok, msg = run_git_pull_then_push()
    safe_msg = urllib.parse.quote(msg)
    if ok:
        return redirect(f"/?message={safe_msg}&type=success")
    return redirect(f"/?message={urllib.parse.quote('Git 拉取后推送失败：' + msg)}&type=warning")


@app.route("/push", methods=["POST"])
def push():
    ok, msg = run_git_push()
    safe_msg = urllib.parse.quote(msg)

    if ok:
        return redirect(f"/?message={safe_msg}&type=success")

    return redirect(f"/?message={urllib.parse.quote('Git 推送失败：' + msg)}&type=warning")


if __name__ == "__main__":
    url = "http://127.0.0.1:5000"

    def open_browser():
        webbrowser.open(url)

    threading.Timer(1.2, open_browser).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
