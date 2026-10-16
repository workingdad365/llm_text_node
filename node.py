"""ComfyUI text input/output node."""

from .providers import BASES, EFFORTS, generate


class LLMText:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "system_prompt": ("STRING", {"multiline": True, "default": "You are a helpful assistant. Return only the requested text."}),
            "provider": (list(BASES),),
            "api_key": ("STRING", {"default": "", "tooltip": "API key, or leave empty to use the server environment variable. Entered keys are stored in the workflow."}),
            "model": ("STRING", {"default": "", "tooltip": "Enter a model ID or use Fetch Models and select a model."}),
            "max_tokens": ("INT", {"default": 4096, "min": 16, "max": 1048576, "tooltip": "Output budget, including reasoning on some providers."}),
            "timeout": ("INT", {"default": 120, "min": 10, "max": 1800}),
            "reasoning": (["Minimum", "Model default", *EFFORTS], {"tooltip": "Minimum selects the lowest supported reasoning setting. Explicit unsupported efforts return an error."}),
            "provider_slug": ("STRING", {"default": "", "tooltip": "OpenRouter only. Exact provider slugs, comma-separated. Restricts routing to these providers."}),
            "routing_suffix": (["Default", ":nitro", ":floor"], {"tooltip": "OpenRouter only. Nitro prioritizes throughput; floor prioritizes price. Default preserves a suffix entered in Model."}),
            "cache_response": ("BOOLEAN", {"default": True, "tooltip": "Reuse the result for unchanged inputs. Turn off to request a new response every run."}),
            "text": ("STRING", {"multiline": True, "defaultInput": True, "default": "", "tooltip": "Connect the external STRING to process. Convert to a widget for manual input."}),
        }}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "execute"
    CATEGORY = "LLM/Text"
    DESCRIPTION = "Process text using OpenAI, Gemini, Anthropic, or OpenRouter."

    @classmethod
    def IS_CHANGED(cls, cache_response=True, **kwargs):
        return "cached" if cache_response else float("nan")

    def execute(self, cache_response=True, **kwargs):
        return (generate(**kwargs),)


NODE_CLASS_MAPPINGS = {"LLMText": LLMText}
NODE_DISPLAY_NAME_MAPPINGS = {"LLMText": "LLM Text"}
