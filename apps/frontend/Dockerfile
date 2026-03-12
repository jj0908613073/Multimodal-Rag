FROM node:18-alpine AS builder

WORKDIR /app
# 預期在此目錄下會有 package.json
# 未來開發時，可透過 `docker-compose run frontend npm create vite@latest . --template react-ts` 來初始化
COPY . .

# 為符合 initial 架構，我們提供一個簡單的 fallback 網頁
RUN echo "<html><body><h1>Multimodal RAG Frontend - Starting...</h1><p>Please initialize React here.</p></body></html>" > index.html

# 簡單的開發伺服器 (開發階段掛載)
CMD ["npx", "http-server", "-p", "80", "."]
