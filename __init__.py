from .node import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

WEB_DIRECTORY = "./web"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]


def register_routes():
    try:
        from server import PromptServer
    except ImportError:
        return
    if not hasattr(PromptServer, "instance"):
        return
    import asyncio
    from aiohttp import web
    from .providers import list_models, native_efforts

    @PromptServer.instance.routes.post("/llm_text/models")
    async def models(request):
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object.")
            provider = body.get("provider", "")
            key = body.get("api_key", "")
            if not isinstance(provider, str) or not isinstance(key, str):
                raise ValueError("Provider and API key must be strings.")
            if provider not in ("OpenAI", "Gemini", "Anthropic", "OpenRouter"):
                raise ValueError("Unknown provider.")
            records = await asyncio.to_thread(list_models, provider, key)
            if provider != "OpenRouter":
                records = [{**record, "llm_text_efforts": native_efforts(provider, record["id"])} for record in records]
            return web.json_response({"models": records})
        except (ValueError, RuntimeError) as exc:
            return web.json_response({"error": str(exc)}, status=400)


register_routes()
