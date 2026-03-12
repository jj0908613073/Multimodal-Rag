# 上傳到 GitHub

## 1. 在電腦上初始化 Git（若尚未初始化）

在專案目錄 `glm-ocr` 下執行：

```powershell
cd c:\Users\User\GLM_OCR\glm-ocr

# 若還沒有 git 倉庫
git init
```

## 2. 加入檔案並第一次提交

```powershell
git add .
git commit -m "Initial commit: GLM-OCR with Word/PPT/Excel/PDF support"
```

## 3. 在 GitHub 建立新倉庫

1. 登入 https://github.com
2. 點右上角 **+** → **New repository**
3. 填寫：
   - **Repository name**：例如 `GLM_OCR` 或 `glm-ocr`
   - **Public**
   - **不要**勾選 "Add a README"（本地已有程式碼）
4. 點 **Create repository**

## 4. 連到 GitHub 並推送

建立好倉庫後，GitHub 會顯示一組指令，或在本機執行（把 `你的帳號` 和 `倉庫名稱` 換成你的）：

```powershell
git remote add origin https://github.com/你的帳號/倉庫名稱.git
git branch -M main
git push -u origin main
```

若使用 SSH：

```powershell
git remote add origin git@github.com:你的帳號/倉庫名稱.git
git branch -M main
git push -u origin main
```

## 注意

- `.gitignore` 已存在，會忽略 `.venv`、`__pycache__`、`/data/`、`/output/` 等，不會把虛擬環境或大檔案傳上去。
- 若 `examples/source/` 裡有測試檔（PDF、PPT 等），若體積大可考慮加入 `.gitignore` 規則或不上傳該資料夾。
