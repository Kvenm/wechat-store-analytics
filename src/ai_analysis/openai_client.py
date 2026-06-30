from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any

from .schema import REPORT_SCHEMA


DEFAULT_MODEL = "gpt-4.1-mini"


@dataclass(frozen=True)
class OpenAIAnalysisClient:
    api_key_env: str = "OPENAI_API_KEY"
    model: str = DEFAULT_MODEL
    temperature: float = 0.2
    dry_run: bool = True

    def generate_shop_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _dry_run_response(
            payload=payload,
            api_key=os.environ.get(self.api_key_env),
            report_type="shop_analysis",
            model=self.model,
            temperature=self.temperature,
            api_key_env=self.api_key_env,
        )

    def generate_multi_shop_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _dry_run_response(
            payload=payload,
            api_key=os.environ.get(self.api_key_env),
            report_type="multi_shop_comparison",
            model=self.model,
            temperature=self.temperature,
            api_key_env=self.api_key_env,
        )


def generate_shop_report(payload: dict[str, Any], *, client: OpenAIAnalysisClient | None = None) -> dict[str, Any]:
    return (client or OpenAIAnalysisClient()).generate_shop_report(payload)


def generate_multi_shop_report(payload: dict[str, Any], *, client: OpenAIAnalysisClient | None = None) -> dict[str, Any]:
    return (client or OpenAIAnalysisClient()).generate_multi_shop_report(payload)


def _dry_run_response(
    *,
    payload: dict[str, Any],
    api_key: str | None,
    report_type: str,
    model: str,
    temperature: float,
    api_key_env: str,
) -> dict[str, Any]:
    messages = _messages(payload, report_type)
    if not api_key:
        return {
            "ok": False,
            "dry_run": True,
            "error": {
                "code": "missing_openai_api_key",
                "message": f"{api_key_env} is not set; no OpenAI request was made.",
            },
            "request": _request_shape(messages, model, temperature),
        }
    return {
        "ok": True,
        "dry_run": True,
        "message": "OpenAI API call is intentionally disabled in this skeleton.",
        "request": _request_shape(messages, model, temperature),
    }


def _messages(payload: dict[str, Any], report_type: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是微信小店经营分析助手。只基于用户提供的聚合脱敏指标输出 JSON，"
                "不要编造原始订单、个人身份、手机号、地址或未提供的数据。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": report_type,
                    "output_schema": REPORT_SCHEMA,
                    "payload": payload,
                },
                ensure_ascii=False,
                indent=2,
            ),
        },
    ]


def _request_shape(messages: list[dict[str, str]], model: str, temperature: float) -> dict[str, Any]:
    return {
        "model": model,
        "temperature": temperature,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "wechat_store_ai_report",
                "schema": REPORT_SCHEMA,
                "strict": True,
            },
        },
        "messages": messages,
    }
