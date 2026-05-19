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

# 默认修改当前目录下的 announcements.json
# 如果你的 announcements.json 在 data/announcements.json，就改成 Path("data/announcements.json")
JSON_FILE = Path("announcements.json")
BACKUP_DIR = Path("json_backups")


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


def run_git_push():
    try:
        subprocess.run(["git", "add", str(JSON_FILE)], check=True)

        commit_msg = f"Update announcements {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        commit_result = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            capture_output=True,
            text=True
        )

        if commit_result.returncode != 0:
            output = (commit_result.stdout or "") + "\n" + (commit_result.stderr or "")
            if "nothing to commit" in output.lower():
                return True, "没有新的修改需要提交。"
            return False, output.strip()

        push_result = subprocess.run(
            ["git", "push", "origin", "main"],
            capture_output=True,
            text=True
        )

        if push_result.returncode != 0:
            return False, (push_result.stderr or push_result.stdout).strip()

        return True, "已经成功推送到 GitHub。"

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
<form method="post" action="/push">
    <button class="push" type="submit">推送 GitHub</button>
</form>
<div class="meta">
这个按钮会执行：git add announcements.json → git commit → git push origin main
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
