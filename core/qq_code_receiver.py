#!/usr/bin/env python3
"""
qq_code_receiver.py - QQ 机器人验证码接收器

通过腾讯官方 QQ 机器人 API 接收私信中的 6 位数字验证码，
自动写入 sms_code.json 供自动化脚本使用。
"""

import re
import json
import asyncio
import logging
import threading
from datetime import datetime

import botpy
from botpy.message import DirectMessage

from core.constants import SMS_CODE_FILE
from core.config import read_sms_code_file

logger = logging.getLogger(__name__)


class CodeReceiverClient(botpy.Client):
    """botpy 客户端 - 处理私信消息中的验证码"""

    async def on_ready(self):
        logger.info(f"QQ Bot 「{self.robot.name}」 已连接")

    async def on_direct_message_create(self, message: DirectMessage):
        """处理私信消息"""
        content = message.content.strip()
        logger.info(f"收到私信: {content}")

        # 判断是否为 6 位数字验证码
        if not re.match(r'^\d{6}$', content):
            logger.debug(f"忽略非验证码消息: {content}")
            return

        # 读取当前 sms_code.json，保持 sent_at 不变
        sms_data = read_sms_code_file()
        sent_at = sms_data.get('sent_at', '')

        # 写入验证码和 filled_at
        now = datetime.now().isoformat()
        new_data = {
            'code': content,
            'sent_at': sent_at,
            'filled_at': now,
        }

        try:
            SMS_CODE_FILE.write_text(
                json.dumps(new_data, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            logger.info(f"验证码已写入: code={content}, filled_at={now}")

            # 回复确认消息
            await message.reply(content=f"✅ 验证码 {content} 已收到")
        except Exception as e:
            logger.error(f"写入验证码失败: {e}")
            try:
                await message.reply(content=f"❌ 写入失败: {e}")
            except Exception:
                pass


class QQCodeReceiver:
    """QQ 验证码接收器 - 在后台线程中运行 botpy"""

    def __init__(self, appid: str, secret: str):
        self.appid = appid
        self.secret = secret
        self._loop = None
        self._thread = None
        self._client = None
        self._running = False

    def start(self):
        """启动后台线程运行 botpy"""
        if self._running:
            logger.warning("QQ Bot 已在运行中")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="qq-bot")
        self._thread.start()
        logger.info("QQ Bot 后台线程已启动")

    def stop(self):
        """停止 QQ Bot"""
        self._running = False
        if self._loop and self._loop.is_running():
            # 在事件循环中安排关闭
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("QQ Bot 已停止")

    async def _shutdown(self):
        """优雅关闭 botpy 客户端"""
        if self._client:
            await self._client.close()

    def _run_loop(self):
        """在新线程中运行 asyncio 事件循环"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._loop.run_until_complete(self._run_bot())
        except Exception as e:
            logger.error(f"QQ Bot 运行异常: {e}", exc_info=True)
        finally:
            self._loop.close()
            self._running = False

    async def _run_bot(self):
        """运行 botpy 客户端"""
        intents = botpy.Intents(direct_message=True)
        self._client = CodeReceiverClient(intents=intents)

        try:
            await self._client.start(appid=self.appid, secret=self.secret)
        except Exception as e:
            logger.error(f"QQ Bot 启动失败: {e}", exc_info=True)
