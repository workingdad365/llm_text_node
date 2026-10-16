import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const LEGACY_WIDGETS = ["text", "provider", "api_key", "model", "system_prompt", "max_tokens", "timeout", "reasoning", "provider_slug", "routing_suffix", "cache_response"];
const EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"];
const TRANSIENT_WIDGETS = new Set(["Fetch Models", "Available Models", "Model Info"]);
const PROVIDER_SETTINGS = ["model", "system_prompt", "max_tokens", "timeout", "reasoning", "provider_slug", "routing_suffix", "cache_response"];

function savedSettings(provider) {
    try {
        const settings = JSON.parse(localStorage.getItem(`LLMText.settings.${provider}`));
        return settings && typeof settings === "object" && !Array.isArray(settings) ? settings : null;
    } catch {
        return null;
    }
}

function restoreNumericInputs(node) {
    for (const [name, fallback, min, max] of [["max_tokens", 4096, 16, 1048576], ["timeout", 120, 10, 1800]]) {
        const widget = node.widgets.find((item) => item.name === name);
        const value = typeof widget.value === "string" && widget.value.trim() ? Number(widget.value) : widget.value;
        widget.value = Number.isInteger(value) && value >= min && value <= max ? value : fallback;
    }
}

function savedKey(provider) {
    try {
        return localStorage.getItem(`LLMText.apiKey.${provider}`);
    } catch {
        return null;
    }
}

function maskKey(value) {
    if (!value) return "";
    return value.length <= 8 ? "*".repeat(value.length) : `${value.slice(0, 4)}${"*".repeat(Math.min(value.length - 8, 20))}${value.slice(-4)}`;
}

function addKeyWidget(node) {
    const previous = node.widgets.find((widget) => widget.name === "api_key");
    const position = node.widgets.indexOf(previous);
    let key = String(previous.value || "");
    let editing = false;
    const input = document.createElement("input");
    input.type = "text";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = "API key or server environment variable";
    input.setAttribute("aria-label", "API Key");
    input.style.cssText = "width:100%;box-sizing:border-box;background:var(--comfy-input-bg,#222);color:var(--input-text,#ddd);border:1px solid #555;border-radius:5px;padding:6px;font:inherit;";
    const sync = () => { input.value = editing ? key : maskKey(key); };
    const widget = node.addDOMWidget("api_key", "text", input, {
        getHeight: () => 48,
        getMinHeight: () => 48,
        getMaxHeight: () => 48,
        getValue: () => key,
        setValue: (value) => { key = String(value ?? ""); sync(); },
    });
    widget.serializeValue = () => key;
    input.addEventListener("focus", () => { editing = true; input.type = "password"; sync(); });
    input.addEventListener("input", () => {
        node.graph?.beforeChange();
        widget.value = input.value;
        const provider = node.widgets.find((item) => item.name === "provider").value;
        try {
            localStorage.setItem(`LLMText.apiKey.${provider}`, key);
            input.title = "API key saved in this browser for this provider.";
        } catch {
            input.title = "Browser storage unavailable. Save the workflow to retain this API key.";
        }
        node.graph?.afterChange();
        node.setDirtyCanvas(true, true);
    });
    input.addEventListener("blur", () => { editing = false; sync(); input.type = "text"; });
    previous.onRemove?.();
    node.widgets.splice(node.widgets.indexOf(widget), 1);
    node.widgets.splice(position, 1, widget);
    sync();
}

app.registerExtension({
    name: "LLMText.Controls",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "LLMText") return;
        const configure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (info) {
            configure?.apply(this, arguments);
            let names = info.properties?.llm_text_widget_names || LEGACY_WIDGETS;
            const values = info.widgets_values || [];
            const persistedNames = names.filter((name) => !TRANSIENT_WIDGETS.has(name));
            if (values.length === persistedNames.length) names = persistedNames;
            const named = info.widgets_values_named ?? Object.fromEntries(names.slice(0, values.length).map((name, index) => [name, values[index]]));
            Object.entries(named).forEach(([name, value]) => {
                const widget = this.widgets.find((item) => item.name === name);
                if (widget && widget.serialize !== false) widget.value = value;
            });
            restoreNumericInputs(this);
            const provider = this.widgets.find((item) => item.name === "provider").value;
            const key = savedKey(provider);
            if (key !== null) this.widgets.find((item) => item.name === "api_key").value = key;
            this.llmTextSyncProvider?.();
            this.llmTextUpdateInfo?.();
        };
        const serialize = nodeType.prototype.onSerialize;
        nodeType.prototype.onSerialize = function (info) {
            serialize?.apply(this, arguments);
            info.properties ??= {};
            const widgets = this.widgets.filter((widget) => widget.serialize !== false);
            info.properties.llm_text_widget_names = widgets.map((widget) => widget.name);
            info.widgets_values = widgets.map((widget) => widget.value);
            info.widgets_values_named = Object.fromEntries(widgets.map((widget) => [widget.name, widget.value]));
        };
        const original = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = original?.apply(this, arguments);
            addKeyWidget(this);
            const find = (name) => this.widgets.find((widget) => widget.name === name);
            let activeProvider = find("provider").value;
            const defaults = Object.fromEntries(PROVIDER_SETTINGS.map((name) => [name, find(name).value]));
            const saveSettings = () => {
                const settings = Object.fromEntries(PROVIDER_SETTINGS.map((name) => [name, find(name).value]));
                try {
                    localStorage.setItem(`LLMText.settings.${activeProvider}`, JSON.stringify(settings));
                } catch {}
            };
            const restoreSettings = () => {
                const settings = savedSettings(activeProvider);
                if (!settings) return;
                for (const name of PROVIDER_SETTINGS) {
                    const value = settings[name];
                    if (typeof value === typeof defaults[name]) find(name).value = value;
                }
                restoreNumericInputs(this);
            };
            this.llmTextSyncProvider = () => { activeProvider = find("provider").value; restoreSettings(); };
            const labels = { text: "Input Text", provider: "Provider", api_key: "API Key", model: "Model",
                system_prompt: "System Prompt", max_tokens: "Max Output Tokens", timeout: "Timeout (seconds)",
                reasoning: "Reasoning Effort", provider_slug: "OpenRouter Provider Slug",
                routing_suffix: "OpenRouter Routing Suffix", cache_response: "Cache Response" };
            for (const [name, label] of Object.entries(labels)) { if (find(name)) find(name).label = label; }
            let records = [];
            let requestId = 0;
            const picker = this.addWidget("combo", "Available Models", "Fetch models first", (value) => {
                if (!records.some((record) => record.id === value)) return;
                find("model").value = value;
                find("reasoning").value = "Minimum";
                updateInfo();
                saveSettings();
                this.setDirtyCanvas(true, true);
            }, { values: ["Fetch models first"], serialize: false });
            const updateInfo = () => {
                const selected = String(find("model").value || "");
                picker.options.values = [...new Set([
                    records.length ? "Select a model" : "Fetch models first",
                    ...(selected ? [selected] : []), ...records.map((entry) => entry.id),
                ])];
                picker.value = selected || picker.options.values[0];
                const id = String(find("model").value).replace(/:(nitro|floor)$/, "");
                const record = records.find((entry) => entry.id === id);
                const reasoning = record?.reasoning;
                const provider = find("provider").value;
                let efforts = record?.llm_text_efforts || {
                    OpenAI: EFFORTS.slice(0, -1), Gemini: ["minimal", "low", "medium", "high"],
                    Anthropic: ["none", "low", "medium", "high", "max"], OpenRouter: EFFORTS,
                }[provider] || [];
                if (provider === "OpenRouter" && record) {
                    efforts = reasoning ? EFFORTS.filter((effort) => effort === "none"
                        ? reasoning.mandatory === false
                        : reasoning.supported_efforts === null || reasoning.supported_efforts?.includes(effort)) : [];
                }
                const effortWidget = find("reasoning");
                effortWidget.options ??= {};
                effortWidget.options.values = ["Minimum", "Model default", ...efforts];
                if (!effortWidget.options.values.includes(effortWidget.value)) effortWidget.value = "Minimum";
            };
            this.llmTextUpdateInfo = updateInfo;
            const fetchButton = this.addWidget("button", "Fetch Models", null, async () => {
                const current = ++requestId;
                const provider = find("provider").value;
                const key = find("api_key").value;
                fetchButton.label = "Loading models...";
                this.setDirtyCanvas(true, true);
                try {
                    const response = await api.fetchApi("/llm_text/models", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ provider, api_key: key }),
                    });
                    const data = await response.json();
                    if (current !== requestId || provider !== find("provider").value || key !== find("api_key").value) return;
                    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
                    records = data.models;
                    updateInfo();
                    if (!records.length && !find("model").value) {
                        picker.options.values = ["No models found"];
                        picker.value = "No models found";
                    }
                } catch (error) {
                    if (current === requestId && provider === find("provider").value && key === find("api_key").value) {
                        const message = `Model discovery failed: ${error.message}`;
                        if (app.extensionManager?.toast?.add) {
                            app.extensionManager.toast.add({ severity: "error", summary: "LLM Text", detail: message, life: 10000 });
                        } else {
                            app.ui?.dialog?.show(message);
                        }
                    }
                } finally {
                    if (current === requestId) fetchButton.label = "Fetch Models";
                    this.setDirtyCanvas(true, true);
                }
            }, { serialize: false });
            for (const widget of [picker, fetchButton]) widget.serialize = false;
            const controls = [fetchButton, picker];
            this.widgets = this.widgets.filter((widget) => !controls.includes(widget));
            this.widgets.splice(this.widgets.indexOf(find("model")) + 1, 0, ...controls);
            const systemWidget = find("system_prompt");
            this.widgets.splice(this.widgets.indexOf(systemWidget), 1);
            this.widgets.unshift(systemWidget);
            const textWidget = find("text");
            if (textWidget) {
                textWidget.options ??= {};
                textWidget.options.getHeight = () => 32;
                textWidget.options.getMinHeight = () => 32;
                textWidget.options.getMaxHeight = () => 32;
                this.widgets.splice(this.widgets.indexOf(textWidget), 1);
                this.widgets.splice(1, 0, textWidget);
            }
            const providerWidget = find("provider");
            const previous = providerWidget.callback;
            providerWidget.callback = (...args) => {
                saveSettings();
                previous?.apply(providerWidget, args);
                activeProvider = providerWidget.value;
                requestId++;
                fetchButton.label = "Fetch Models";
                records = [];
                picker.options.values = ["Fetch models first"];
                picker.value = "Fetch models first";
                find("model").value = "";
                find("api_key").value = savedKey(providerWidget.value) ?? "";
                find("reasoning").value = "Minimum";
                restoreSettings();
                updateInfo();
                this.setDirtyCanvas(true, true);
            };
            const modelWidget = find("model");
            modelWidget.hidden = true;
            modelWidget.type = "hidden";
            modelWidget.computeSize = () => [0, -4];
            modelWidget.computeLayoutSize = () => ({ minHeight: 0, maxHeight: 0, minWidth: 0 });
            const modelCallback = modelWidget.callback;
            modelWidget.callback = (...args) => { modelCallback?.apply(modelWidget, args); find("reasoning").value = "Minimum"; updateInfo(); };
            for (const name of PROVIDER_SETTINGS) {
                const widget = find(name);
                const callback = widget.callback;
                widget.callback = (...args) => { callback?.apply(widget, args); saveSettings(); };
            }
            const key = savedKey(providerWidget.value);
            if (key !== null) find("api_key").value = key;
            if (savedSettings(activeProvider)) restoreSettings();
            updateInfo();
            this.setSize([480, Math.max(this.size[1], 600)]);
            return result;
        };
    },
});
