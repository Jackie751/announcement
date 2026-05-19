from flask import Flask, request, redirect, render_template_string
import json
from pathlib import Path
from datetime import date
import webbrowser
import threading

app = Flask(__name__)

JSON_FILE = Path("announcements.json")


def load_data():
    if not JSON_FILE.exists():
        return []

    text = JSON_FILE.read_text(encoding="utf-8").strip()
    if not text:
        return []

    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        return []
    except json.JSONDecodeError:
        return []


def save_data(data):
    JSON_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>公告 JSON 编辑器</title>
<style>
body {
    font-family: Arial, "Microsoft YaHei", sans-serif;
    background: #151527;
    color: #f5f5ff;
    padding: 30px;
}
.container {
    max-width: 900px;
    margin: auto;
}
h1 {
    color: #8be9ff;
}
form, .card {
    background: #22223b;
    padding: 20px;
    border-radius: 16px;
    margin-bottom: 20px;
}
input, select, textarea {
    width: 100%;
    box-sizing: border-box;
    margin: 8px 0 15px;
    padding: 10px;
    border-radius: 10px;
    border: 1px solid #555;
    background: #11111f;
    color: white;
    font-size: 15px;
}
textarea {
    min-height: 160px;
}
button {
    border: none;
    border-radius: 999px;
    padding: 10px 18px;
    background: #7ddcff;
    color: #111;
    font-weight: bold;
    cursor: pointer;
}
.delete {
    background: #ff5f7e;
    color: white;
}
.meta {
    color: #aaa;
    font-size: 14px;
}
pre {
    white-space: pre-wrap;
    line-height: 1.6;
}
</style>
</head>
<body>
<div class="container">
<h1>公告 JSON 本地编辑器</h1>

<form method="post" action="/add">
    <label>标题</label>
    <input name="title" required>

    <label>日期</label>
    <input name="date" value="{{ today }}" required>

    <label>分类</label>
    <select name="category">
        <option value="更新">更新</option>
        <option value="重要">重要</option>
        <option value="维护">维护</option>
        <option value="资源">资源</option>
        <option value="其他">其他</option>
    </select>

    <label>内容</label>
    <textarea name="content" required></textarea>

    <label>图片链接，可不填</label>
    <input name="image">

    <label>
        <input type="checkbox" name="important" style="width:auto;">
        标记为重要
    </label>

    <br><br>
    <button type="submit">保存公告</button>
</form>

<h2>已有公告</h2>

{% for item in data %}
<div class="card">
    <h3>{{ loop.index }}. {{ item.title }}</h3>
    <div class="meta">
        日期：{{ item.date }} |
        分类：{{ item.category }} |
        重要：{{ item.important }}
    </div>
    <pre>{{ item.content }}</pre>
    {% if item.image %}
    <div class="meta">图片：{{ item.image }}</div>
    {% endif %}

    <form method="post" action="/delete/{{ loop.index0 }}" style="padding:0; background:none;">
        <button class="delete" type="submit">删除</button>
    </form>
</div>
{% endfor %}
</div>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(
        HTML,
        data=load_data(),
        today=str(date.today())
    )


@app.route("/add", methods=["POST"])
def add():
    data = load_data()

    item = {
        "title": request.form.get("title", "").strip(),
        "date": request.form.get("date", "").strip(),
        "category": request.form.get("category", "").strip(),
        "content": request.form.get("content", "").strip(),
        "image": request.form.get("image", "").strip(),
        "important": request.form.get("important") == "on"
    }

    data.insert(0, item)
    save_data(data)

    return redirect("/")


@app.route("/delete/<int:index>", methods=["POST"])
def delete(index):
    data = load_data()

    if 0 <= index < len(data):
        data.pop(index)
        save_data(data)

    return redirect("/")


if __name__ == "__main__":
    url = "http://127.0.0.1:5000"

    def open_browser():
        webbrowser.open(url)

    threading.Timer(1.2, open_browser).start()

    app.run(host="127.0.0.1", port=5000, debug=False)