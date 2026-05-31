# Git 使用指南

## 常用命令

### 基础操作
```bash
git status                        # 查看当前状态
git log --oneline -10             # 查看最近10条提交记录
git diff                          # 查看未暂存的改动
```

### 拉取 / 推送
```bash
git pull origin main              # 拉取远程最新
git push origin main              # 推送到远程
git fetch origin                  # 只拉取不合并
git reset --hard origin/main      # 强制同步到远程（丢弃本地改动）
```

### 提交
```bash
git add .                         # 暂存所有改动
git add 文件名                    # 暂存指定文件
git commit -m "说明"              # 提交
git push origin main              # 推送
```

### 撤销 / 回退
```bash
git checkout -- 文件名            # 撤销单个文件的未提交改动
git reset --hard HEAD             # 撤销所有未提交改动
git reset --hard HEAD~1           # 回退到上一个提交（危险，会丢失提交）
git revert HEAD                   # 安全回退（新建一个反向提交）
```

---

## 清理 Git 历史（瘦身）

> 适用于 JSON 文件频繁更新导致仓库体积过大的情况

### 方法一：保留最近N次提交（推荐）
```bash
# 只保留最近 50 次提交，清除之前所有历史
git log --oneline | tail -n +51 | awk '{print $1}' | xargs git rebase --onto

# 更简单的方式：孤儿分支重建
git checkout --orphan temp_branch       # 新建无历史的孤儿分支
git add .                               # 暂存所有文件
git commit -m "clean history"           # 初始提交
git branch -D main                      # 删除旧 main
git branch -m main                      # 重命名当前为 main
git push -f origin main                 # 强制推送（清除远程历史）
```

### 方法二：删除指定文件的所有历史记录
```bash
git filter-branch --force --index-filter \
  "git rm --cached --ignore-unmatch 文件名.json" \
  --prune-empty --tag-name-filter cat -- --all

git push origin --force --all
```

### 方法三：本地清理垃圾对象（Windows 版）
```powershell
# 出现 Deletion of directory failed 时用这个代替 git prune-packed
Remove-Item -Recurse -Force .git\objects\
git init
git fetch origin
git reset --hard origin/main
```

---

## 常见问题

### push 被拒绝（non-fast-forward）
```bash
git pull --rebase origin main     # 先 rebase 再推
git push origin main
```

### 本地和远程冲突不想保留本地
```bash
git fetch origin
git reset --hard origin/main      # 直接丢弃本地，以远程为准
```

### 误删文件想找回
```bash
git checkout HEAD -- 文件名       # 从最近一次提交恢复
```

### 查看远程地址
```bash
git remote -v
```

### 修改远程地址
```bash
git remote set-url origin https://github.com/用户名/仓库名.git
```

### 大文件推送慢 / 超时
```bash
git config http.postBuffer 524288000   # 调大缓冲区到 500MB
```

### Windows 下 .git/objects 删不掉
```powershell
Remove-Item -Recurse -Force .git\objects\
git init
git fetch origin
git reset --hard origin/main
```

---

## 本项目快速维护流程

```bash
# 日常推送（服务器端 bot 自动推送，一般不需要手动）
git add .
git commit -m "update"
git push origin main

# 本地同步最新
git fetch origin
git reset --hard origin/main

# 仓库体积过大时清理（每隔几个月做一次）
git checkout --orphan temp_branch
git add .
git commit -m "clean history"
git branch -D main
git branch -m main
git push -f origin main
```