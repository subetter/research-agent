"""Shared Chat Completions transport for chat and structured research calls."""
import time
from urllib.parse import urlparse
import httpx
from .tracing import get_tracing, reasoning_tokens


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


def _record_generation(settings, name, messages, body=None, error=None, started=None, extra=None):
    usage = (body or {}).get("usage") or {}
    get_tracing().generation(
        name,
        model=settings.llm_model,
        input={"messages": messages, **(extra or {})},
        output=None if error else (body or {}).get("choices"),
        usage={
            "input": usage.get("prompt_tokens") or usage.get("input_tokens") or 0,
            "output": usage.get("completion_tokens") or usage.get("output_tokens") or 0,
            "total": usage.get("total_tokens") or 0,
        },
        metadata={
            "latency_ms": int((time.perf_counter() - started) * 1000) if started else None,
            "reasoning_tokens": reasoning_tokens(usage),
            "error": error,
        },
        level="ERROR" if error else None,
    )


async def complete(settings, messages, tools=None, json_output=False):
    payload = completion_payload(settings, messages, tools, json_output)
    started = time.perf_counter()
    try:
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
        _record_generation(settings, "llm.complete", messages, body=body, started=started, extra={"json_output": json_output, "tools": bool(tools)})
        # Keep reasoning_content intact in persisted research operations and tool-loop history.
        return body
    except Exception as exc:
        _record_generation(settings, "llm.complete", messages, error=str(exc)[:300], started=started, extra={"json_output": json_output, "tools": bool(tools)})
        raise


async def stream_complete(settings, messages):
    """Yield content/usage only; reasoning deltas are never sent to the UI."""
    import json
    payload = completion_payload(settings, messages)
    payload.update(stream=True, stream_options={"include_usage": True})
    done = False
    nonempty = False
    finish = None
    collected = []
    usage = {}
    started = time.perf_counter()
    error = None
    try:
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
                        usage = body["usage"]
                        yield {"tokens": usage.get("total_tokens", 0)}
                    for choice in body.get("choices", []):
                        if choice.get("index", 0) != 0:
                            continue
                        text = (choice.get("delta") or {}).get("content")
                        if isinstance(text, str) and text:
                            nonempty = nonempty or bool(text.strip())
                            collected.append(text)
                            yield {"text": text}
                        finish = choice.get("finish_reason") or finish
        if not done or finish != "stop":
            raise ValueError("模型输出被截断或连接中断；已保留收到的内容")
        if not nonempty:
            raise ValueError("模型返回空消息")
    except Exception as exc:
        error = str(exc)[:300]
        raise
    finally:
        _record_generation(
            settings,
            "llm.stream_complete",
            messages,
            body={"choices": [{"message": {"content": "".join(collected)}}], "usage": usage} if collected or usage else None,
            error=error,
            started=started,
        )
