# -*- coding: utf-8 -*-
"""
美股流动性监控 - 手机端多通道推送适配器 (Mobile Push Notifier)
================================================================
支持手机端即时推送通道：
  1. 微信服务号直推: WxPusher / PushPlus / Server酱 (手机无需装新App，微信直接弹卡片)
  2. 苹果 iOS 极简即时推送: Bark (iPhone 原生锁屏弹窗 + 铃声)
  3. 移动办公机器人: 企业微信 (WeCom) / 飞书 (Feishu) / 钉钉 (DingTalk)
  4. 跨平台/海外: Telegram Bot

机制设计：
  - 内置智能防抖 (Debounce)：同一告警级别在 10 分钟内不重复轰炸手机。
  - 自动读取 push_config.json 或环境变量。
"""

import os
import sys
import time
import json
import logging
import requests
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List

logger = logging.getLogger("MobilePushNotifier")
CONFIG_FILE = Path(__file__).resolve().parent / "push_config.json"
DEBOUNCE_FILE = Path(__file__).resolve().parent / ".push_debounce_cache.json"
ENV_FILE = Path(__file__).resolve().parent / ".env"

def _load_env():
    """加载本地 .env 文件至环境变量"""
    if ENV_FILE.exists():
        try:
            for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
        except Exception:
            pass

_load_env()

class MobilePushNotifier:
    def __init__(self, config_path: Optional[Path] = None):
        _load_env()
        self.config_path = config_path or CONFIG_FILE
        self.config = self._load_config()
        self.debounce_seconds = self.config.get("debounce_seconds", 7200)  # 默认2小时(7200秒)防抖
        self._sent_cache = self._load_debounce_cache()

    def _load_debounce_cache(self) -> Dict[str, Any]:
        """从本地 JSON 文件读取防抖记录，实现跨进程持久化"""
        if DEBOUNCE_FILE.exists():
            try:
                return json.loads(DEBOUNCE_FILE.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"读取防抖缓存 {DEBOUNCE_FILE} 异常: {e}")
        return {}

    def _save_debounce_cache(self):
        """持久化保存防抖记录到本地 JSON"""
        try:
            DEBOUNCE_FILE.write_text(json.dumps(self._sent_cache, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"保存防抖缓存 {DEBOUNCE_FILE} 失败: {e}")

    def _load_config(self) -> Dict[str, Any]:
        """加载推送配置，优先读取 .env 与环境变量，合并本地 push_config.json"""
        default_config = {
            "enabled": True,
            "debounce_seconds": 7200,
            "channels": {
                # 1. 微信端直推 (免装新App)
                "wxpusher_app_token": os.environ.get("WXPUSHER_APP_TOKEN", ""),
                "wxpusher_uids": [x for x in os.environ.get("WXPUSHER_UIDS", "").split(",") if x],
                "pushplus_token": os.environ.get("PUSHPLUS_TOKEN", ""),
                "serverchan_sendkey": os.environ.get("SERVERCHAN_SENDKEY", ""),
                # 2. 苹果 iOS Bark 推送
                "bark_url": os.environ.get("BARK_URL", ""),
                # 3. 移动办公 App 机器人
                "wecom_webhook": os.environ.get("WECOM_WEBHOOK", ""),
                "dingtalk_webhook": os.environ.get("DINGTALK_WEBHOOK", ""),
                "feishu_webhook": os.environ.get("FEISHU_WEBHOOK", ""),
                # 4. Telegram
                "telegram_bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
                "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID", "")
            }
        }
        if self.config_path.exists():
            try:
                user_cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
                if "enabled" in user_cfg:
                    default_config["enabled"] = bool(user_cfg["enabled"])
                if "debounce_seconds" in user_cfg:
                    default_config["debounce_seconds"] = int(user_cfg["debounce_seconds"])
                if "channels" in user_cfg and isinstance(user_cfg["channels"], dict):
                    for k, v in user_cfg["channels"].items():
                        env_val = default_config["channels"].get(k, "")
                        # 优先保留有效的非空配置；若两者皆有，选用较长更完整的值（防无效占位符）
                        if v and (not env_val or len(str(v)) >= len(str(env_val))):
                            default_config["channels"][k] = v
                        elif env_val:
                            default_config["channels"][k] = env_val
            except Exception as e:
                logger.warning(f"读取推送配置 {self.config_path} 异常: {e}")
        else:
            try:
                self.config_path.write_text(json.dumps(default_config, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        return default_config

    def _is_duplicate(
        self,
        dedup_key: str,
        level: str = "",
        score: float = 0.0,
        is_resonance: bool = False,
        force: bool = False
    ) -> bool:
        if force:
            self._record_sent(dedup_key, level, score, is_resonance)
            return False

        now = time.time()
        record = self._sent_cache.get(dedup_key, {})
        if not isinstance(record, dict):
            record = {"time": float(record) if record else 0.0, "level": "", "score": 0.0, "resonance": False}

        last_time = float(record.get("time", 0.0))
        last_level = str(record.get("level", ""))
        last_score = float(record.get("score", 0.0))
        last_res = bool(record.get("resonance", False))

        # 状态恶化跃迁突破防抖检查：
        # 1. 之前未共振，当前触发共振
        # 2. 综合分相比上次飙升 >= 10分
        # 3. 状态等级升级至高危共振警戒
        is_breakthrough = False
        if is_resonance and not last_res:
            is_breakthrough = True
        elif score - last_score >= 10.0:
            is_breakthrough = True
        elif "高危共振" in level and "高危共振" not in last_level:
            is_breakthrough = True

        if not is_breakthrough and (now - last_time < self.debounce_seconds):
            return True

        self._record_sent(dedup_key, level, score, is_resonance)
        return False

    def _record_sent(self, dedup_key: str, level: str, score: float, is_resonance: bool):
        self._sent_cache[dedup_key] = {
            "time": time.time(),
            "level": level,
            "score": score,
            "resonance": is_resonance,
            "formatted_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self._save_debounce_cache()

    def send_alert(
        self,
        title: str,
        content: str,
        level: str = "ALERT",
        score: float = 0.0,
        is_resonance: bool = False,
        force: bool = False
    ) -> Dict[str, Any]:
        """
        统一发送手机端信息推送 (带持久化防抖与恶化跃迁穿透)
        """
        if not self.config.get("enabled", True):
            return {"ok": False, "msg": "推送功能已全局禁用"}

        dedup_key = f"{level}:{title}"
        if self._is_duplicate(dedup_key, level=level, score=score, is_resonance=is_resonance, force=force):
            return {"ok": False, "msg": f"命中防抖窗口({self.debounce_seconds}秒)，且无恶化跃迁，暂不重复推送"}


        channels = self.config.get("channels", {})
        results = {}

        # 1. 微信 WxPusher
        wx_token = channels.get("wxpusher_app_token")
        wx_uids = channels.get("wxpusher_uids", [])
        if wx_token and wx_uids:
            results["wxpusher"] = self._send_wxpusher(wx_token, wx_uids, title, content)

        # 2. 微信 PushPlus
        pp_token = channels.get("pushplus_token")
        if pp_token:
            results["pushplus"] = self._send_pushplus(pp_token, title, content)

        # 3. 微信 Server酱
        sc_key = channels.get("serverchan_sendkey")
        if sc_key:
            results["serverchan"] = self._send_serverchan(sc_key, title, content)

        # 4. 苹果 iOS Bark
        bark_url = channels.get("bark_url")
        if bark_url:
            results["bark"] = self._send_bark(bark_url, title, content)

        # 5. 企业微信
        wecom_url = channels.get("wecom_webhook")
        if wecom_url:
            results["wecom"] = self._send_wecom(wecom_url, title, content)

        # 6. 钉钉
        ding_url = channels.get("dingtalk_webhook")
        if ding_url:
            results["dingtalk"] = self._send_dingtalk(ding_url, title, content)

        # 7. 飞书
        feishu_url = channels.get("feishu_webhook")
        if feishu_url:
            results["feishu"] = self._send_feishu(feishu_url, title, content)

        # 8. Telegram
        tg_token = channels.get("telegram_bot_token")
        tg_chat = channels.get("telegram_chat_id")
        if tg_token and tg_chat:
            results["telegram"] = self._send_telegram(tg_token, tg_chat, title, content)

        if not results:
            return {"ok": False, "msg": "未配置任何有效的手机端推送通道 (请配置 Bark/微信/企业微信/钉钉/飞书 等)"}

        return {"ok": True, "details": results}

    # ================== 各通道具体适配实现 ==================

    def _send_bark(self, base_url: str, title: str, content: str):
        """Bark (iOS 极简推送客户端)"""
        try:
            url = base_url.rstrip("/")
            payload = {
                "title": title,
                "body": content,
                "group": "美股流动性监控",
                "sound": "alarm.caf",
                "icon": "https://img.icons8.com/color/96/bullish.png"
            }
            resp = requests.post(url, json=payload, timeout=6)
            return resp.status_code == 200
        except Exception as e:
            logger.warning(f"Bark 发送失败: {e}")
            return False

    def _send_wecom(self, webhook_url: str, title: str, content: str):
        """企业微信机器人"""
        try:
            markdown_text = f"### {title}\n{content}\n> 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            payload = {"msgtype": "markdown", "markdown": {"content": markdown_text}}
            resp = requests.post(webhook_url, json=payload, timeout=6)
            return resp.status_code == 200
        except Exception as e:
            logger.warning(f"企业微信发送失败: {e}")
            return False

    def _send_dingtalk(self, webhook_url: str, title: str, content: str):
        """钉钉机器人"""
        try:
            markdown_text = f"### {title}\n\n{content}\n\n> 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            payload = {
                "msgtype": "markdown",
                "markdown": {"title": title, "text": markdown_text}
            }
            resp = requests.post(webhook_url, json=payload, timeout=6)
            return resp.status_code == 200
        except Exception as e:
            logger.warning(f"钉钉发送失败: {e}")
            return False

    def _send_feishu(self, webhook_url: str, title: str, content: str):
        """飞书机器人"""
        try:
            payload = {
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {"tag": "plain_text", "content": title},
                        "template": "red" if "高危" in title or "警报" in title else "orange"
                    },
                    "elements": [
                        {"tag": "markdown", "content": content},
                        {"tag": "hr"},
                        {"tag": "note", "elements": [{"tag": "plain_text", "content": f"巡检时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"}]}
                    ]
                }
            }
            resp = requests.post(webhook_url, json=payload, timeout=6)
            return resp.status_code == 200
        except Exception as e:
            logger.warning(f"飞书发送失败: {e}")
            return False

    def _send_wxpusher(self, app_token: str, uids: List[str], title: str, content: str):
        """WxPusher (微信公众号直推)"""
        try:
            url = "https://wxpusher.zjiecode.com/api/send/message"
            payload = {
                "appToken": app_token,
                "content": f"## {title}\n\n{content}",
                "summary": title[:20],
                "contentType": 3,  # 3: markdown
                "uids": uids
            }
            resp = requests.post(url, json=payload, timeout=6)
            return resp.status_code == 200
        except Exception as e:
            logger.warning(f"WxPusher 发送失败: {e}")
            return False

    def _send_pushplus(self, token: str, title: str, content: str):
        """PushPlus (推送加 - 微信直推)"""
        try:
            url = "https://www.pushplus.plus/send"
            payload = {
                "token": token,
                "title": title,
                "content": content,
                "template": "markdown"
            }
            headers = {"Content-Type": "application/json"}
            resp = requests.post(url, json=payload, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 200:
                    logger.info(f"PushPlus 微信推送成功: {data.get('data')}")
                    return True
                else:
                    logger.warning(f"PushPlus 微信推送被拒: code={data.get('code')}, msg={data.get('msg')}")
                    return False
            return False
        except Exception as e:
            logger.warning(f"PushPlus 发送失败: {e}")
            return False

    def _send_serverchan(self, sendkey: str, title: str, content: str):
        """Server酱 (Turbo 版 - 微信直推)"""
        try:
            url = f"https://sctapi.ftqq.com/{sendkey}.send"
            payload = {"title": title, "desp": content}
            resp = requests.post(url, data=payload, timeout=6)
            return resp.status_code == 200
        except Exception as e:
            logger.warning(f"Server酱发送失败: {e}")
            return False

    def _send_telegram(self, bot_token: str, chat_id: str, title: str, content: str):
        """Telegram Bot"""
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            text = f"*{title}*\n\n{content}"
            payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
            resp = requests.post(url, json=payload, timeout=8)
            return resp.status_code == 200
        except Exception as e:
            logger.warning(f"Telegram 发送失败: {e}")
            return False
