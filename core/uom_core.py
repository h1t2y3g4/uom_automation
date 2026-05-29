#!/usr/bin/env python3
"""
uom_core.py - 向后兼容 re-export 外观模块

所有公开符号已拆分到以下子模块：
  - constants      路径常量、URL
  - time_utils     时间/日期工具
  - config         配置加载、JSON 工具
  - airspace_data  空域数据解析/规范化
  - airspace_query 空域在线查询、缓存
  - auth           登录认证
  - ui_helpers     浏览器 UI 交互
  - fly_plan       飞行计划表单操作
  - takeoff        起飞确认
  - context        浏览器上下文管理

本文件仅做 re-export，保持 `import core.uom_core as core` 的向后兼容。
"""

# 标准库 re-export（部分 CLI 脚本通过 core.time / core.datetime 访问）
import time
from datetime import datetime

# 子模块全部 re-export
from core.constants import *  # noqa: F401,F403
from core.time_utils import *  # noqa: F401,F403
from core.config import *  # noqa: F401,F403
from core.airspace_data import *  # noqa: F401,F403
from core.auth import *  # noqa: F401,F403
from core.ui_helpers import *  # noqa: F401,F403
from core.fly_plan import *  # noqa: F401,F403
from core.takeoff import *  # noqa: F401,F403
from core.context import *  # noqa: F401,F403
from core.airspace_query import *  # noqa: F401,F403
