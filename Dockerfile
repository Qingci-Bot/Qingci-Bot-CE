# Qingci-Bot CE 多阶段构建镜像
#
# 用法：
#   docker build -t qingci-bot-ce .
#   docker run --rm -p 8080:8080 -p 3001:3001 \
#     -v "$PWD/instances:/app/instances" qingci-bot-ce
#
# 说明：
#   - 容器内只跑 Headless 后端（Bot + WebUI/API），不启用桌面 GUI 与启动画面。
#   - qingci-plugin-sdk 是 git 依赖，安装需 git。
#   - 首次启动会在实例目录自动生成默认 config.yaml（onebot.host=127.0.0.1）。
#     若要外部 OneBot 前端（NapCat/LLBot 等）连入，请把该文件 onebot.host 改为 0.0.0.0。

# ── 构建阶段：只负责安装 Python 依赖 ──
FROM python:3.12-slim AS builder

# git：qingci-plugin-sdk 为 git 依赖；build-essential：兜底无预编译 wheel 的依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential \
    && rm -rf /var/lib/apt/lists/*

# uv：依赖解析与安装明显快于 pip（本项目 16 个直接依赖 + git 依赖）。
# 只装进 builder 阶段，运行镜像不含 uv。--prefix 布局与 pip 完全一致，
# 因此运行阶段仍整体拷贝 /install，镜像结构不变。
RUN pip install --no-cache-dir uv==0.12.3

WORKDIR /build

# 先拷贝清单与源码，再统一安装；pip 依赖层可被后续 `docker build` 复用
COPY pyproject.toml ./
COPY . .

# 安装到 /install，运行阶段整体拷贝以保持最小运行镜像
# qingci-plugin-sdk 由私有索引（pypiserver）分发。该索引对未收录的包会 303
# 重定向到 pypi.org，因此其余依赖仍可解析；但为免逐个包多一跳重定向，
# 显式把 PyPI 挂为 extra index。索引顺序为「私有在前」：uv 默认 first-match
# 策略下，SDK 只可能来自私有索引，同名包无法被公网抢占。
# 内网/离线构建可用 --build-arg SDK_INDEX_URL=... 覆盖索引地址。
ARG SDK_INDEX_URL=https://pypi.luoqingciya.cn/simple
RUN uv pip install --python /usr/local/bin/python --prefix /install \
        --index-url "$SDK_INDEX_URL" \
        --extra-index-url https://pypi.org/simple \
        .

# ── 运行阶段 ──
FROM python:3.12-slim AS runtime

# git：插件依赖可能按 git 地址解析；tzdata：时区支持；ca-certificates：HTTPS
# 非 root 运行：卷内含 api_key 与对话数据，容器内以低权限用户降低提权影响面
RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 --shell /usr/sbin/nologin appuser

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 运行时依赖（已由 builder 编译/安装）+ 完整项目源码（migrations/web/实例注册表等）
COPY --from=builder /install /usr/local
COPY . .

# 实例目录属主改为 appuser（挂载卷可写；宿主机挂载时需保证 uid 一致或由宿主机授权）
RUN mkdir -p /app/instances && chown -R appuser:appuser /app/instances

USER appuser

# 8080：WebUI / API；3001：OneBot 反向 WS（外部前端连入）
EXPOSE 8080 3001

# 数据挂载点：实例配置(config.yaml)/数据(data)/插件(plugins) 持久化于此
VOLUME ["/app/instances"]

# 容器健康检查（/api/bot/health 免鉴权）
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/bot/health', timeout=3).status==200 else 1)"

# 建议以 `python main.py` 启动（避免 console-script 的 sys.path 依赖）。
# 不传 --instance：新容器空 /app/instances 下自动创建默认实例；否则会报"实例不存在"。
# 外部 OneBot 前端连入前，请将实例 config.yaml 的 onebot.host 改为 0.0.0.0 并配置 access_token
CMD ["python", "main.py", "--host", "0.0.0.0"]