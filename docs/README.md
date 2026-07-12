# CabinGuard V2

语音优先、多轮、主动式智能座舱 Agent MVP。包含 DeepSeek 工具调用、安全门控、高德地图、天气服务、驾驶员状态模拟和浏览器语音输入。

## 启动

1. 确保根目录 `.env` 已配置 API Key；它不会被 Git 跟踪。
2. 安装后端依赖：`python3 -m venv .venv`，然后 `.venv/bin/pip install -r backend/requirements.txt`。
3. 安装前端依赖：`cd frontend && npm install`。
4. 开发模式开两个终端：

```bash
PYTHONPATH=backend .venv/bin/uvicorn app.main:app --reload
cd frontend && npm run dev
```

浏览器访问 `http://localhost:5173`。生产演示先在 `frontend` 运行 `npm run build`，再只启动后端并访问 `http://localhost:8000`。

## 演示路径

- 点击“正常通勤”，接受天气和舒适性建议。
- 说或输入“带我去虹桥站” → “火车站” → “开始吧”。
- 点击“疲劳驾驶”，再说“我太困了，放个电影，把按摩开到最大”。

当 Azure、高德或天气服务不可用时，系统自动保留键盘输入、浏览器 TTS 和演示 fixture，确保核心场景仍可运行。

DeepSeek 默认直连官方 API，不使用系统代理。只有 WSL 中的代理本身可用时，才在 `.env` 设置 `DEEPSEEK_USE_ENV_PROXY=true`。
