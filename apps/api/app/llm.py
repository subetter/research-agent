"""Shared Chat Completions transport for chat and structured research calls."""
from urllib.parse import urlparse
import httpx


def is_deepseek(settings):
    return settings.llm_provider == "deepseek" or urlparse(settings.llm_base_url).hostname == "api.deepseek.com"


def completion_payload(settings, messages, tools=None, json_output=False):
    payload = {"model": settings.llm_model, "messages": messages,
               "max_tokens": settings.llm_max_tokens, "stream": False}
    if tools:
        payload["tools"] = tools
    elif json_output:
        payload["response_format"] = {"type": "json_object"}
    if is_deepseek(settings):
        payload["thinking"] = {"type": settings.deepseek_thinking}
        if settings.deepseek_thinking == "enabled":
            payload["reasoning_effort"] = settings.deepseek_reasoning_effort
    return payload


async def complete(settings, messages, tools=None, json_output=False):
    payload = completion_payload(settings, messages, tools, json_output)
    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        response = await client.post(settings.llm_base_url.rstrip("/") + "/chat/completions",
                                     headers={"Authorization": f"Bearer {settings.llm_api_key}"}, json=payload)
        if response.is_error:
            messages_by_status = {
                400: "模型请求参数不兼容，请检查模型名称、思考模式与输出上限",
                401: "模型 API Key 无效，请检查本地配置",
                402: "模型服务余额不足，请检查 API 平台余额",
                429: "模型请求频率过高，请稍后重试",
                500: "模型服务暂时异常，请稍后重试",
                503: "模型服务暂时繁忙，请稍后重试",
            }
            raise ValueError(messages_by_status.get(response.status_code, f"模型服务请求失败（HTTP {response.status_code}）"))
        body = response.json()
    choices = body.get("choices") or []
    if not choices:
        raise ValueError("模型没有返回回复")
    if choices[0].get("finish_reason") == "length":
        raise ValueError("模型输出被截断，请调整输出上限或缩小研究范围")
    message = choices[0]["message"]
    if not message.get("tool_calls") and not (isinstance(message.get("content"), str) and message["content"].strip()):
        raise ValueError("模型返回空消息")
    # Keep reasoning_content intact in persisted research operations and tool-loop history.
    return body


async def stream_complete(settings, messages):
    """Yield content/usage only; reasoning deltas are never sent to the UI."""
    import json
    payload = completion_payload(settings, messages)
    payload.update(stream=True, stream_options={"include_usage": True})
    done = False
    nonempty = False
    finish = None
    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        async with client.stream("POST", settings.llm_base_url.rstrip("/") + "/chat/completions",
                                 headers={"Authorization": f"Bearer {settings.llm_api_key}"}, json=payload) as response:
            if response.is_error:
                raise ValueError({401: "模型 API Key 无效", 402: "模型服务余额不足", 429: "模型请求频率过高，请稍后重试"}.get(response.status_code, "模型流式请求失败，请检查服务配置"))
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    done = True
                    break
                if not data:
                    continue
                try:
                    body = json.loads(data)
                except ValueError:
                    raise ValueError("模型流式响应格式错误") from None
                if body.get("error"):
                    raise ValueError("模型生成途中失败")
                if body.get("usage"):
                    yield {"tokens": body["usage"].get("total_tokens", 0)}
                for choice in body.get("choices", []):
                    if choice.get("index", 0) != 0:
                        continue
                    text = (choice.get("delta") or {}).get("content")
                    if isinstance(text, str) and text:
                        nonempty = nonempty or bool(text.strip())
                        yield {"text": text}
                    finish = choice.get("finish_reason") or finish
    if not done or finish != "stop":
        raise ValueError("模型输出被截断或连接中断；已保留收到的内容")
    if not nonempty:
        raise ValueError("模型返回空消息")
