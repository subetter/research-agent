"""Run with apps/api/.venv/bin/python scripts/check_model.py [--chat]."""
import argparse
import asyncio
from pathlib import Path
import sys
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
from app.config import settings
from app.llm import complete


async def check(chat):
    if not settings.llm_api_key or not settings.llm_model:
        print("尚未配置模型 API Key。请在项目 .env 文件填写 LLM_API_KEY。")
        return 1
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(settings.llm_base_url.rstrip("/") + "/models",
                                        headers={"Authorization": f"Bearer {settings.llm_api_key}"})
            if response.is_error:
                print(f"模型列表检查失败：HTTP {response.status_code}。请检查 Key、余额和服务地址。")
                return 1
            model_ids = [model["id"] for model in response.json().get("data", [])]
        if settings.llm_model not in model_ids:
            print("当前模型不在服务返回的模型列表中，请检查 LLM_MODEL。")
            return 1
        print(f"认证和模型检查通过：{settings.llm_model}")
        if chat:
            result = await complete(settings, [{"role": "user", "content": "请只回复：连接成功"}])
            print("生成请求成功，消耗 Token：", result.get("usage", {}).get("total_tokens", 0))
        return 0
    except (httpx.HTTPError, ValueError, KeyError):
        print("连接或回复格式检查失败，请检查网络及模型配置。密钥未输出。")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--chat", action="store_true", help="额外执行一次按 Token 计费的简短生成请求")
    sys.exit(asyncio.run(check(parser.parse_args().chat)))
