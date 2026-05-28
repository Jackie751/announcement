# init_pages.py
# 把现有 arktips.json 拆成分页文件，运行一次即可
# 运行完后 git push，本地生成的分页文件可以删除

import json
from pathlib import Path

BASE_DIR  = Path(__file__).resolve().parent
PAGE_SIZE = 100

src = BASE_DIR / "arktips.json"
if not src.exists():
    print("arktips.json 不存在")
    exit(1)

data = json.loads(src.read_text(encoding="utf-8"))
if not isinstance(data, list):
    print("arktips.json 格式不对，不是数组")
    exit(1)

print(f"共 {len(data)} 条数据，开始拆分...")

# 按100条一页拆分（保持原顺序，最新的在第1页）
pages = []
for i in range(0, len(data), PAGE_SIZE):
    pages.append(data[i:i + PAGE_SIZE])

total_pages = len(pages)

# 写分页文件
for i, page_data in enumerate(pages):
    page_num  = i + 1
    page_file = BASE_DIR / f"arktips-{page_num}.json"
    page_file.write_text(
        json.dumps(page_data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"  arktips-{page_num}.json — {len(page_data)} 条")

# 写 index
current_page  = total_pages
current_count = len(pages[-1])

index = {
    "total": len(data),
    "pages": total_pages,
    "files": [f"arktips-{i+1}.json" for i in range(total_pages)]
}
(BASE_DIR / "arktips-index.json").write_text(
    json.dumps(index, ensure_ascii=False, indent=2),
    encoding="utf-8"
)
print(f"  arktips-index.json — {total_pages} 页")

# 写 counter（记录当前写到哪一页哪一条）
counter = {
    "current_page":  current_page,
    "current_count": current_count
}
(BASE_DIR / "arktips-counter.json").write_text(
    json.dumps(counter, ensure_ascii=False, indent=2),
    encoding="utf-8"
)
print(f"  arktips-counter.json — 当前第 {current_page} 页，已有 {current_count} 条")

print(f"\n完成！共生成 {total_pages} 个分页文件")
print("现在运行 git push 把这些文件推到 GitHub")
print("推完后可以删除本地的 arktips-*.json / arktips-index.json / arktips-counter.json")