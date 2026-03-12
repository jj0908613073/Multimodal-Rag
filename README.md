## Multimodal-RAG 簡介

企業級的多模態 RAG (Retrieval-Augmented Generation) 解決方案架構，支援各種大型視覺/語言模型 (vLLM, Ollama 等) 的動態切換，同時處理文字、影像與表格等複雜版面。

目前已額外整合：

- **glm-ocr**：用於版面偵測 (PP-Structure) 與文件結構切分
- **Docling**：做 PDF/DOCX 解析與結構化抽取
- **OpenCC**：在輸出前將內容由簡體自動轉為繁體

---

## 一、只跑「本機文件解析」的快速使用方式

這是你現在最常用的模式：在本機或公司電腦上，把文件丟進某個資料夾，產生 Markdown + 圖片輸出。

### 1. 安裝 Python 依賴

在專案根目錄：

```bash
pip install -r requirements.txt  # 如果你有匯出
```

或至少安裝核心依賴（範例）：

```bash
pip install docling opencc-python-reimplemented python-dotenv requests
pip install -e ./glm-ocr
```

> **說明**：`pip install -e ./glm-ocr` 會把 `glmocr` 安裝為套件，方便在任何機器上匯入。

### 2. 設定 `.env`

從範例複製一份：

```bash
cp .env.example .env
```

至少確認下列參數（可依實際環境調整）：

- **`MODEL_PROVIDER=ollama`**
- **`OLLAMA_ENDPOINT=http://localhost:11434`**（或公司內部的 Ollama 伺服器）
- **`MODEL_NAME=glm-ocr`**（或你實際拉下來的多模態模型名稱，例如 `glm-4v:latest`、`qwen2-vl:latest`）

### 3. 準備輸入 / 輸出資料夾（皆為相對路徑）

在專案根目錄：

- **輸入**：`data/input`  
  把要解析的 `.pdf` / `.docx` 丟進這個資料夾（可含子資料夾）。
- **輸出**：`data/processed`  
  每個文件會產生對應子資料夾與 `.md` 檔，以及 `images/` 圖片。

這些路徑都是 **相對於專案根目錄**，搬到任何電腦都可以使用。

### 4. 執行批次解析

在專案根目錄：

```bash
python -m src.processors.document_processor
```

預設等同於：

- `--input-dir ./data/input`
- `--output-dir ./data/processed`

若要自訂資料夾，可以指定參數，例如：

```bash
python -m src.processors.document_processor \
  --input-dir "./my_docs" \
  --output-dir "./data/output_md"
```

> **注意**：所有路徑皆為相對路徑，只要專案資料夾結構相同，換電腦不需要改程式碼。

---

## 二、完整系統架構（前後端 + 基礎設施）

當你需要啟動前端、後端 API 與向量資料庫時，可使用 `docker-compose`：

- **Frontend**：`apps/frontend/`（React/Vite UI）
- **Backend API**：`apps/backend/`（FastAPI，提供 RAG API、上傳端點與任務排程）
- **核心 RAG 邏輯**：`src/`（包含 `processors`, `chunking`, `vectorstores`, `retrieval`, `model_clients` 等模組）
- **基礎設施**：Redis（快取與任務 Queue）、Milvus（向量資料庫）、MinIO（物件與原始文件儲存）

### 啟動全部服務

1. 準備 `.env`（同上）
2. 在專案根目錄執行：

```bash
docker-compose up -d --build
```

所有 volume 與路徑同樣採用相對路徑（例如 `./data`、`./storage`），搬到其他機器時只要整個專案資料夾結構維持一致即可。

---

## 目錄結構簡述

- **`src/`**：核心邏輯（包含 `processors/document_processor.py`、`model_clients/` 等）
- **`glm-ocr/`**：外部整合的 glm-ocr 專案（僅保留作為 layout detector 等功能）
- **`data/input`**：待解析的原始文件
- **`data/processed`**：解析後輸出的 Markdown 與圖片
- **`apps/`**：前端與後端服務（若只在本機做文件解析，可忽略）
- **`docker-compose.yml`**：一鍵啟動完整系統的設定

更細節的設計與拆分可參考 `implementation_plan.md`。*** End Patch```"/>
