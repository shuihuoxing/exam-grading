"""DeepSeek 客户端（兼容 OpenAI 接口）。

统一封装：调用 + JSON 输出解析 + 重试 + 超时。所有 LLM 调用走这里。
"""
from __future__ import annotations
import json
import re
import time

from openai import OpenAI

from ..config import settings
from ..models import LLMError

# 计时日志：始终输出到 stderr，便于定位慢请求
def _log(msg: str) -> None:
    import sys
    print(f"[llm] {msg}", file=sys.stderr, flush=True)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.deepseek_api_key:
            raise LLMError("未配置 DEEPSEEK_API_KEY，请在 backend/.env 设置。")
        _client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=30.0,        # 单次调用上限 30s；卡住时快速失败而非干等
            max_retries=0,        # 关闭 SDK 内部重试，避免与我自己的 retries 循环叠加放大耗时
        )
    return _client


def _strip_json(text: str) -> str:
    """模型偶尔会用 ```json ... ``` 包裹，去掉围栏。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def chat_json(system: str, user: str, retries: int = 1) -> dict:
    """让模型返回 JSON。失败重试 retries 次，仍失败抛 LLMError。"""
    client = _get_client()
    last_err: Exception | None = None
    user_len = len(user or "")
    for attempt in range(retries + 1):
        t0 = time.time()
        _log(f"call attempt={attempt+1}/{retries+1} model={settings.deepseek_model.strip()} user_chars={user_len}")
        try:
            resp = client.chat.completions.create(
                model=settings.deepseek_model.strip(),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                # 让模型尽量输出纯 JSON
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            content = resp.choices[0].message.content or ""
            data = json.loads(_strip_json(content))
            _log(f"ok in {time.time()-t0:.1f}s")
            return data
        except Exception as e:  # noqa: BLE001
            _log(f"fail after {time.time()-t0:.1f}s: {type(e).__name__}: {str(e)[:150]}")
            last_err = e
    raise LLMError(f"LLM 调用失败（已重试{retries}次）：{last_err}")


def chat_text(system: str, user: str, retries: int = 1) -> str:
    """让模型返回纯文本。失败返回空字符串，不阻塞主流程。"""
    client = _get_client()
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        t0 = time.time()
        _log(f"chat_text attempt={attempt+1} model={settings.deepseek_model.strip()}")
        try:
            resp = client.chat.completions.create(
                model=settings.deepseek_model.strip(),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.3,
            )
            text = (resp.choices[0].message.content or "").strip()
            _log(f"chat_text ok in {time.time()-t0:.1f}s")
            return text
        except Exception as e:  # noqa: BLE001
            _log(f"chat_text fail after {time.time()-t0:.1f}s: {type(e).__name__}: {str(e)[:150]}")
            last_err = e
    _log(f"chat_text 给出空结果（重试耗尽）: {last_err}")
    return ""
