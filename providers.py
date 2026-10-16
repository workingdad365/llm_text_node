"""Text-only provider clients and model discovery."""

import re
import logging
from copy import deepcopy
import threading
import time

import requests


BASES = {
    "OpenAI": "https://api.openai.com/v1",
    "Gemini": "https://generativelanguage.googleapis.com/v1beta",
    "Anthropic": "https://api.anthropic.com/v1",
    "OpenRouter": "https://openrouter.ai/api/v1",
}
EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
_catalog = None
_catalog_lock = threading.Lock()


class APIError(RuntimeError):
    def __init__(self, message, status, detail):
        super().__init__(message)
        self.status = status
        self.detail = detail


def resolve_key(provider, api_key):
    if provider not in BASES:
        raise ValueError("Unknown provider.")
    key = (api_key or "").strip()
    if not key:
        raise ValueError(f"Enter an API key for {provider}.")
    return key


def headers(provider, key):
    result = {"Content-Type": "application/json"}
    if provider == "Gemini":
        result["x-goog-api-key"] = key
        result["Api-Revision"] = "2026-05-20"
    elif provider == "Anthropic":
        result.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    else:
        result["Authorization"] = f"Bearer {key}"
    if provider == "OpenRouter":
        result["X-Title"] = "ComfyUI LLM Text"
    return result


def request_json(provider, key, method, path, timeout=120, **kwargs):
    try:
        response = requests.request(
            method, BASES[provider] + path, headers=headers(provider, key),
            timeout=(10, timeout), allow_redirects=False, **kwargs,
        )
    except requests.RequestException:
        raise RuntimeError(f"{provider} connection failed or timed out.") from None
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(f"{provider} returned invalid JSON (HTTP {response.status_code}).") from None
    if not isinstance(data, dict):
        raise RuntimeError(f"{provider} returned an invalid response object.")
    if not 200 <= response.status_code < 300 or data.get("error"):
        error = data.get("error", {})
        detail = error.get("message", "Request failed.") if isinstance(error, dict) else str(error)
        detail = str(detail).replace(key, "[redacted]") if key else str(detail)
        raise APIError(f"{provider} HTTP {response.status_code}: {detail[:600]}", response.status_code, detail)
    return data


def list_models(provider, api_key=""):
    global _catalog
    key = resolve_key(provider, api_key) if provider != "OpenRouter" else api_key.strip()
    records, cursors = [], set()
    params = {"pageSize": 1000} if provider == "Gemini" else {}
    if provider == "Anthropic":
        params = {"limit": 1000}
    while True:
        data = request_json(provider, key, "GET", "/models", timeout=30, params=params)
        items = data.get("models" if provider == "Gemini" else "data")
        if not isinstance(items, list):
            raise RuntimeError("Invalid model catalog response.")
        for item in items:
            if provider == "Gemini" and "generateContent" not in item.get("supportedGenerationMethods", []):
                continue
            if provider == "OpenRouter":
                architecture = item.get("architecture") or {}
                if "text" not in architecture.get("output_modalities", ["text"]):
                    continue
            model_id = item.get("name", "").removeprefix("models/") if provider == "Gemini" else item.get("id", "")
            if model_id:
                records.append({**item, "id": model_id})
        cursor = data.get("nextPageToken") if provider == "Gemini" else (
            data.get("last_id") if provider == "Anthropic" and data.get("has_more") else None
        )
        if provider == "Anthropic" and data.get("has_more") and not cursor:
            raise RuntimeError("Missing model pagination cursor.")
        if not cursor:
            break
        if cursor in cursors:
            raise RuntimeError("Repeated model pagination cursor.")
        cursors.add(cursor)
        params["pageToken" if provider == "Gemini" else "after_id"] = cursor
    records = sorted({item["id"]: item for item in records}.values(), key=lambda item: item["id"])
    if provider == "OpenRouter":
        with _catalog_lock:
            _catalog = (time.monotonic(), records)
    return records


def base_model(model):
    for suffix in (":nitro", ":floor"):
        if model.endswith(suffix):
            return model[:-len(suffix)]
    return model


def openrouter_model(model):
    with _catalog_lock:
        cached = _catalog
    records = cached[1] if cached and time.monotonic() - cached[0] < 900 else list_models("OpenRouter")
    return next((item for item in records if item["id"] == base_model(model)), {})


def reasoning_options(record, mode):
    result = {"exclude": True}
    if mode == "Model default":
        return result
    info = record.get("reasoning")
    if mode in EFFORTS:
        if not isinstance(info, dict):
            raise ValueError("This OpenRouter model does not advertise reasoning controls. Use Minimum or Model default.")
        if mode == "none":
            if info.get("mandatory") is not False:
                raise ValueError("This model cannot be confirmed to support disabling reasoning.")
            return {**result, "enabled": False}
        supported = info.get("supported_efforts", [])
        if supported is not None and mode not in supported:
            raise ValueError(f"This model does not support reasoning effort '{mode}'.")
        return {**result, "effort": mode}
    if not isinstance(info, dict):
        return result
    if info.get("mandatory") is False:
        return {**result, "enabled": False}
    if "supported_efforts" in info:
        supported = info["supported_efforts"]
        allowed = EFFORTS if supported is None else supported
        # Unknown mandatory state must not be interpreted as permission to disable.
        effort = next((value for value in EFFORTS[1:] if value in allowed), None)
        if effort:
            result["effort"] = effort
    return result


def native_reasoning_options(provider, effort):
    if effort == "Model default":
        return {}
    if provider == "OpenAI":
        return {"reasoning": {"effort": effort}}
    if provider == "Gemini":
        return {"generation_config": {"thinking_level": effort}}
    if effort == "none":
        return {"thinking": {"type": "disabled"}}
    return {"thinking": {"type": "adaptive"}, "output_config": {"effort": effort}}


def native_efforts(provider, model):
    if provider == "OpenAI":
        if re.match(r"^o[134](?:-|$)", model):
            return ["low", "medium", "high"]
        if re.match(r"^gpt-5(?:-(?:mini|nano))?(?:-20\d\d-\d\d-\d\d)?$", model):
            return ["minimal", "low", "medium", "high"]
        return EFFORTS[:-1]
    if provider == "Gemini":
        if model.startswith(("gemini-4", "gemini-3.8", "gemini-3.1-pro", "gemini-2.5")):
            return ["low", "medium", "high"]
        if model.startswith("gemini-3-pro"):
            return ["low", "high"]
        return ["minimal", "low", "medium", "high"]
    return ["none", "low", "medium", "high", "max"]


def native_request(provider, key, path, body, timeout, mode, model):
    efforts = native_efforts(provider, model)
    if mode not in ("Minimum", "Model default") and mode not in efforts:
        raise ValueError(f"{provider} reasoning options for this model: {', '.join(efforts)}.")
    candidates = efforts + ["Model default"] if mode == "Minimum" else [mode]
    for index, effort in enumerate(candidates):
        payload = deepcopy(body)
        for name, value in native_reasoning_options(provider, effort).items():
            payload.setdefault(name, {}).update(value)
        try:
            return request_json(provider, key, "POST", path, timeout, json=payload)
        except APIError as exc:
            # 추론 옵션 자체를 거절한 400 응답에만 다음 최저 설정을 시도함.
            rejected = exc.status == 400 and re.search(r"reasoning|thinking|effort|output_config", exc.detail, re.I)
            if mode != "Minimum" or not rejected or index == len(candidates) - 1:
                raise
            logging.getLogger(__name__).warning("%s rejected reasoning setting %s; trying %s.", provider, effort, candidates[index + 1])


def generate(provider, api_key, model, text, system_prompt, max_tokens,
             timeout, reasoning, provider_slug="", routing_suffix="Default"):
    key = resolve_key(provider, api_key)
    if reasoning not in ("Minimum", "Model default", *EFFORTS):
        raise ValueError("Unknown reasoning mode.")
    model = model.strip()
    if not model or not text.strip():
        raise ValueError("Model and input text are required.")
    if routing_suffix not in ("Default", ":nitro", ":floor"):
        raise ValueError("Unknown routing suffix.")
    if provider == "Gemini":
        model = model.removeprefix("models/")
        body = {"model": model, "input": text, "store": False,
                "generation_config": {"max_output_tokens": max_tokens}}
        if system_prompt:
            body["system_instruction"] = system_prompt
        data = native_request(provider, key, "/interactions", body, timeout, reasoning, model)
        outputs = [step for step in data.get("steps", []) if step.get("type") == "model_output"]
        result = "".join(part.get("text", "") for part in (outputs[-1].get("content", []) if outputs else []) if part.get("type") == "text")
        stop = data.get("status")
    elif provider == "Anthropic":
        body = {"model": model, "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": text}]}
        if system_prompt:
            body["system"] = system_prompt
        data = native_request(provider, key, "/messages", body, timeout, reasoning, model)
        result = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        stop = data.get("stop_reason")
    elif provider == "OpenAI":
        body = {"model": model, "input": text, "max_output_tokens": max_tokens, "store": False}
        if system_prompt:
            body["instructions"] = system_prompt
        data = native_request(provider, key, "/responses", body, timeout, reasoning, model)
        result = "".join(part.get("text", "") for item in data.get("output", [])
                         if item.get("type") == "message" for part in item.get("content", [])
                         if part.get("type") == "output_text")
        stop = (data.get("incomplete_details") or {}).get("reason") or data.get("status")
    else:
        record = openrouter_model(model) if reasoning != "Model default" else {}
        if routing_suffix != "Default":
            model = base_model(model) + routing_suffix
        messages = ([{"role": "system", "content": system_prompt}] if system_prompt else [])
        messages.append({"role": "user", "content": text})
        body = {"model": model, "messages": messages, "max_tokens": max_tokens,
                "reasoning": reasoning_options(record, reasoning), "stream": False}
        slugs = [slug.strip() for slug in provider_slug.split(",") if slug.strip()]
        if slugs:
            body["provider"] = {"only": slugs, "allow_fallbacks": False}
        data = request_json(provider, key, "POST", "/chat/completions", timeout, json=body)
        choices = data.get("choices", [])
        choice = choices[0] if choices else {}
        content = choice.get("message", {}).get("content")
        result = content if isinstance(content, str) else "".join(
            part.get("text", "") for part in (content or []) if part.get("type") == "text")
        stop = choice.get("finish_reason")
    if stop in ("length", "max_tokens", "MAX_TOKENS", "max_output_tokens", "incomplete"):
        raise RuntimeError("Output token limit reached. Increase Max Output Tokens (reasoning may share this budget).")
    if not result.strip():
        raise RuntimeError(f"{provider} returned no text (stop reason: {stop or 'unknown'}).")
    return result
