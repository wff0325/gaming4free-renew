# 采用微软官方预装好 Chromium 和所有底层图形库的 Playwright 镜像
FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy

WORKDIR /app

# 安装虚拟屏幕(xvfb)，因为代码写了 headless=False，在云端无显示器环境下必须靠虚拟显示器运行
RUN apt-get update && apt-get install -y xvfb && rm -rf /var/lib/apt/lists/*

COPY . /app

# 安装必要的依赖库
RUN pip install --no-cache-dir requests playwright patchright 2>/dev/null || pip install --no-cache-dir requests playwright

# 核心：使用 xvfb-run 虚拟屏幕启动，外层死循环每 5 分钟 (300 秒) 跑一次 app.py
CMD ["sh", "-c", "while true; do echo \"[$(date '+%Y-%m-%d %H:%M:%S')] 启动投票流程...\"; xvfb-run -a python -u app.py; echo \"[$(date '+%Y-%m-%d %H:%M:%S')] 本轮结束，等待 5 分钟后进行下一轮...\"; sleep 300; done"]
