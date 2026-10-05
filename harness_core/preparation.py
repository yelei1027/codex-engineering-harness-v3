"""Local preparation stage diagnostics; never an approval or success authority."""
import os
from pathlib import Path

from .common import HarnessError, json_bytes, read_json, safe_id, write_json
from .service_reports import validate_output


STAGES = {
    'UNKNOWN': ('准备阶段无法核验', '保留已有文件，运行 harness preflight；核对诊断日志后使用相同目录和版本重试。'),
    'STARTUP': ('准备程序启动', '先运行 harness preflight；预检通过后重试 harness install --prepare。'),
    'NODE': ('Node 组件依赖（npm ci）', '运行 harness preflight 核对 Node/npm；确认 npm 包源可访问后重试 harness install --prepare。'),
    'PYTHON_ENV': ('MarkItDown Python 环境（uv venv）', '运行 harness preflight 核对 uv；确认 Python 下载源可访问后重试 harness install --prepare。'),
    'PYTHON_PACKAGES': ('MarkItDown Python 依赖（uv pip install）', '确认 Python 包源可访问，再运行 harness install --prepare；已保存的依赖进度和数据会保留。'),
    'MARKITDOWN_SOURCE': ('MarkItDown 固定源码安装', '运行 harness check 核对发行内容；恢复缺失源码后重试 harness install --prepare。'),
    'RUNTIME_BINDING': ('Codex 桌面运行时绑定', '运行 harness preflight 核对桌面运行时；非默认路径需设置 HARNESS_CODEX_BINARY 后重试。'),
    'SIDECARS': ('服务构建入口', '运行 harness preflight 核对 Docker/Compose，再重试 harness install --prepare。'),
    'SERVICES_VERIFY': ('已有服务构建与恢复核验', '保留现有服务和数据；运行 harness check 与 harness services status 核对来源及消费者，来源或所有权变化时使用隔离更新候选，不强行重建或删除。'),
    'POWERCONTEXT_BUILD': ('PowerContext 镜像构建', '运行 harness preflight 核对 Docker；确认镜像下载源可访问后重试 harness install --prepare。'),
    'POWERCONTEXT_CLIENT': ('PowerContext 本地客户端（uv sync）', '运行 harness preflight 核对 uv；确认 Python 包源可访问后重试 harness install --prepare。'),
    'SCREENSHOT_BACKEND': ('截图服务后端镜像构建', '运行 harness preflight 核对 Docker；确认镜像下载源可访问后重试 harness install --prepare。'),
    'SCREENSHOT_FRONTEND': ('截图服务前端镜像构建', '运行 harness preflight 核对 Docker；确认镜像下载源可访问后重试 harness install --prepare。'),
    'SERVICES_START': ('PowerContext/截图服务启动', '运行 harness services status 查看服务及端口冲突；处理冲突后重试 harness install --prepare。'),
    'COMPLETE': ('组件准备收尾', '重试 harness install --prepare；登记和真实调用验收仍需分别核验。'),
}


def path_for(root, operation):
    return Path(root)/'private/install-logs'/(safe_id(operation)+'.status.json')


def start(root, operation):
    path = path_for(root, operation)
    validate_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A collision must preserve the previous file, even if it is regular.
    with path.open('xb') as stream:
        path.chmod(0o600)
        stream.write(json_bytes({'operation': operation, 'root': str(root), 'stage': 'STARTUP'}))


def record(root, stage):
    operation = os.environ.get('HARNESS_PREPARATION_ID')
    if not operation:
        return
    if stage not in STAGES:
        raise HarnessError('Unknown preparation stage')
    path = path_for(root, operation)
    validate_output(path)
    if not path.exists() or path.stat().st_size > 16384:
        raise HarnessError('Preparation diagnostics unavailable; preserve and inspect')
    previous = read_json(path, {})
    if not isinstance(previous, dict) or set(previous) != {'operation','root','stage'} or previous.get('operation') != operation or previous.get('root') != str(root) or not isinstance(previous.get('stage'),str) or previous['stage'] not in STAGES:
        raise HarnessError('Preparation diagnostics changed; preserve and inspect')
    write_json(path, {'operation': operation, 'root': str(root), 'stage': stage})


def failure(root, operation):
    stage = 'UNKNOWN'
    try:
        path = path_for(root, operation)
        validate_output(path)
        if not path.exists() or path.stat().st_size > 16384:
            return '完整组件准备未完成：准备阶段无法核验。下一步：'+STAGES['UNKNOWN'][1]
        value = read_json(path, {})
        if isinstance(value, dict) and set(value) == {'operation','root','stage'} and value.get('operation') == operation and value.get('root') == str(root) and isinstance(value.get('stage'),str) and value['stage'] in STAGES:
            stage = value['stage']
    except (HarnessError, OSError, ValueError):
        pass
    label, action = STAGES[stage]
    return f'完整组件准备未完成：{label}。下一步：{action}'
