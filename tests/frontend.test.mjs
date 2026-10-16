import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";

async function setup(fetchApi, storage = new Map()) {
    let extension;
    const source = (await readFile(new URL("../web/llm_text.js", import.meta.url), "utf8")).replace(/^import .*;\r?\n/gm, "");
    const document = { createElement: () => ({ style: {}, listeners: {}, setAttribute() {},
        addEventListener(name, callback) { this.listeners[name] = callback; } }) };
    const localStorage = { getItem: (name) => storage.get(name) ?? null, setItem: (name, value) => storage.set(name, value) };
    vm.runInNewContext(source, { document, localStorage, app: { registerExtension(value) { extension = value; } }, api: { fetchApi } });
    class Node {
        constructor() {
            this.widgets = Object.entries({ text: "", provider: "OpenRouter", api_key: "key", model: "manual-model", system_prompt: "", max_tokens: 4096,
                timeout: 120, reasoning: "Minimum", provider_slug: "", routing_suffix: "Default", cache_response: true })
                .map(([name, value]) => ({ name, value }));
            this.size = [100, 100];
        }
        addWidget(type, name, value, callback, options) {
            const widget = { type, name, value, callback, options };
            this.widgets.push(widget);
            return widget;
        }
        addDOMWidget(name, type, element, options) {
            const widget = { name, type, element, options };
            Object.defineProperty(widget, "value", { get: options.getValue, set: options.setValue });
            this.widgets.push(widget);
            return widget;
        }
        setDirtyCanvas() {}
        setSize(size) { this.size = size; }
    }
    await extension.beforeRegisterNodeDef(Node, { name: "LLMText" });
    const node = new Node();
    node.onNodeCreated();
    const find = (name) => node.widgets.find((widget) => widget.name === name);
    find.node = node;
    return find;
}

test("model discovery preserves manual model until selected and uses POST", async () => {
    const widget = await setup(async (path, options) => {
        assert.equal(path, "/llm_text/models");
        assert.equal(options.method, "POST");
        assert.equal(JSON.parse(options.body).api_key, "key");
        return { ok: true, json: async () => ({ models: [{ id: "vendor/model", reasoning: { mandatory: true, supported_efforts: ["low", "high"] } }] }) };
    });
    await widget("Fetch Models").callback();
    assert.equal(widget("model").value, "manual-model");
    widget("Available Models").callback("vendor/model");
    assert.equal(widget("model").value, "vendor/model");
    assert.equal(widget("Available Models").value, "vendor/model");
    assert.match(widget("Model Info").value, /required/);
    assert.equal(widget("Available Models").serialize, false);
});

test("layout places system first, input second, and fetch below model", async () => {
    const widget = await setup();
    const names = widget.node.widgets.map((item) => item.name);
    assert.equal(names[0], "system_prompt");
    assert.equal(names[1], "text");
    assert.equal(names[names.indexOf("model") + 1], "Fetch Models");
    assert.equal(widget("model").hidden, true);
});

test("key mask preserves real key for API and serialization, including edits", async () => {
    const widget = await setup();
    const key = widget("api_key");
    key.value = "sk-1234567890-secret-5678";
    assert.match(key.element.value, /^sk-1\*+5678$/);
    assert.equal(key.serializeValue(), "sk-1234567890-secret-5678");
    key.element.listeners.focus();
    assert.equal(key.element.type, "password");
    key.element.value = "new-secret-9876";
    key.element.listeners.input();
    key.element.listeners.blur();
    assert.equal(key.value, "new-secret-9876");
    assert.match(key.element.value, /^new-\*+9876$/);
    key.value = "short";
    assert.equal(key.element.value, "*****");
    key.value = "";
    assert.equal(key.element.value, "");
});

test("typing an API key notifies workflow persistence and restores the unmasked value", async () => {
    const widget = await setup();
    const snapshots = [];
    let beforeChange = 0;
    widget.node.graph = {
        beforeChange() { beforeChange++; },
        afterChange() {
            const saved = {};
            widget.node.onSerialize(saved);
            snapshots.push(saved);
        },
    };
    const key = widget("api_key");
    key.element.listeners.focus();
    key.element.value = "sk-persisted-secret-1234";
    key.element.listeners.input();
    key.element.listeners.blur();
    assert.equal(beforeChange, 1);
    assert.equal(snapshots.length, 1);
    const restored = await setup();
    restored.node.onConfigure(snapshots[0]);
    assert.equal(restored("api_key").value, "sk-persisted-secret-1234");
    assert.equal(restored("api_key").serializeValue(), "sk-persisted-secret-1234");
    assert.ok(!restored("api_key").element.value.includes("secret"));
});

test("API keys persist immediately per provider across reloads and can be cleared", async () => {
    const storage = new Map();
    const widget = await setup(undefined, storage);
    const enter = (value) => {
        const key = widget("api_key");
        key.element.listeners.focus();
        key.element.value = value;
        key.element.listeners.input();
        key.element.listeners.blur();
    };
    enter("router-secret");
    widget("provider").value = "Anthropic";
    widget("provider").callback();
    assert.equal(widget("api_key").value, "");
    enter("anthropic-secret");
    widget("provider").value = "OpenRouter";
    widget("provider").callback();
    assert.equal(widget("api_key").value, "router-secret");
    const restored = await setup(undefined, storage);
    assert.equal(restored("api_key").value, "router-secret");
    restored.node.onConfigure({ properties: { llm_text_widget_names: ["provider", "api_key"] }, widgets_values: ["Anthropic", "stale-secret"] });
    assert.equal(restored("api_key").value, "anthropic-secret");
    enter("");
    const cleared = await setup(undefined, storage);
    cleared.node.onConfigure({ properties: { llm_text_widget_names: ["provider", "api_key"] }, widgets_values: ["OpenRouter", "old-secret"] });
    assert.equal(cleared("api_key").value, "");
});

test("legacy and new workflow values restore by name after reorder", async () => {
    const widget = await setup();
    widget.node.onConfigure({ widgets_values: ["external text", "Anthropic", "real-secret-key", "claude-test", "system text", 8192, 90, "Minimum", "", "Default", false] });
    assert.equal(widget("system_prompt").value, "system text");
    assert.equal(widget("text").value, "external text");
    assert.equal(widget("api_key").value, "real-secret-key");
    assert.equal(widget("provider").value, "Anthropic");
    const saved = {};
    widget.node.onSerialize(saved);
    const restored = await setup();
    restored.node.onConfigure(saved);
    for (const name of ["provider", "api_key", "model", "system_prompt", "text", "max_tokens", "cache_response"]) {
        assert.equal(restored(name).value, widget(name).value);
    }
    assert.ok(!restored("api_key").element.value.includes("secret"));
    assert.equal(restored("Available Models").value, "claude-test");
    assert.ok(restored("Available Models").options.values.includes("claude-test"));
});

test("selected model survives saving and loading without fetching the catalog", async () => {
    const widget = await setup(async () => ({ ok: true, json: async () => ({ models: [{ id: "vendor/selected" }] }) }));
    await widget("Fetch Models").callback();
    widget("Available Models").callback("vendor/selected");
    const saved = {};
    widget.node.onSerialize(saved);
    const restored = await setup();
    restored.node.onConfigure(saved);
    assert.equal(restored("model").value, "vendor/selected");
    assert.equal(restored("Available Models").value, "vendor/selected");
    assert.ok(restored("Available Models").options.values.includes("vendor/selected"));
});

test("old full widget names restore compact native values without shifting numeric fields", async () => {
    const widget = await setup();
    widget("max_tokens").value = 8192;
    widget("timeout").value = 300;
    const names = widget.node.widgets.map((item) => item.name);
    const values = widget.node.widgets.filter((item) => item.serialize !== false).map((item) => item.value);
    const restored = await setup();
    restored.node.onConfigure({ properties: { llm_text_widget_names: names }, widgets_values: values });
    assert.equal(restored("max_tokens").value, 8192);
    assert.equal(restored("timeout").value, 300);
});

test("old placeholder values remain compatible and damaged numbers recover on load", async () => {
    const widget = await setup();
    const names = widget.node.widgets.map((item) => item.name);
    widget("max_tokens").value = 16384;
    widget("timeout").value = 600;
    const values = widget.node.widgets.map((item) => item.serialize === false ? null : item.value);
    widget.node.onConfigure({ properties: { llm_text_widget_names: names }, widgets_values: values });
    assert.equal(widget("max_tokens").value, 16384);
    assert.equal(widget("timeout").value, 600);
    widget.node.onConfigure({ widgets_values_named: { max_tokens: 0, timeout: null } });
    assert.equal(widget("max_tokens").value, 4096);
    assert.equal(widget("timeout").value, 120);
});

test("provider switching and repeated persistence preserve numeric settings", async () => {
    let widget = await setup();
    widget("max_tokens").value = 32768;
    widget("timeout").value = 480;
    for (const provider of ["Anthropic", "Gemini", "OpenAI", "OpenRouter"]) {
        widget("provider").value = provider;
        widget("provider").callback();
        const saved = {};
        widget.node.onSerialize(saved);
        assert.equal(saved.properties.llm_text_widget_names.length, saved.widgets_values.length);
        assert.ok(!saved.properties.llm_text_widget_names.includes("Fetch Models"));
        widget = await setup();
        widget.node.onConfigure(JSON.parse(JSON.stringify(saved)));
        assert.equal(widget("max_tokens").value, 32768);
        assert.equal(widget("timeout").value, 480);
    }
});

test("native model efforts are applied and model changes reset to Minimum", async () => {
    const widget = await setup(async () => ({ ok: true, json: async () => ({ models: [{ id: "gemini-3.8-flash", llm_text_efforts: ["low", "medium", "high"] }] }) }));
    widget("provider").value = "Gemini";
    widget("provider").callback();
    await widget("Fetch Models").callback();
    widget("reasoning").value = "high";
    widget("Available Models").callback("gemini-3.8-flash");
    assert.deepEqual(Array.from(widget("reasoning").options.values), ["Minimum", "Model default", "low", "medium", "high"]);
    assert.equal(widget("reasoning").value, "Minimum");
});

test("provider change clears credentials and ignores pending catalog", async () => {
    let resolve;
    const widget = await setup(() => new Promise((done) => { resolve = done; }));
    const pending = widget("Fetch Models").callback();
    widget("provider").value = "Anthropic";
    widget("provider").callback();
    resolve({ ok: true, json: async () => ({ models: [{ id: "stale" }] }) });
    await pending;
    assert.equal(widget("api_key").value, "");
    assert.equal(widget("model").value, "");
    assert.equal(widget("Available Models").value, "Fetch models first");
});

test("provider settings restore independently after switching and reloading", async () => {
    const storage = new Map();
    const widget = await setup(undefined, storage);
    const router = { model: "vendor/router", system_prompt: "Router instructions", max_tokens: 8192, timeout: 300,
        reasoning: "high", provider_slug: "vendor", routing_suffix: ":nitro", cache_response: false };
    for (const [name, value] of Object.entries(router)) {
        widget(name).value = value;
        widget(name).callback?.();
    }
    widget("provider").value = "Anthropic";
    widget("provider").callback();
    widget("model").value = "claude-test";
    widget("model").callback();
    widget("timeout").value = 600;
    widget("timeout").callback();
    widget("provider").value = "OpenRouter";
    widget("provider").callback();
    for (const [name, value] of Object.entries(router)) assert.equal(widget(name).value, value);
    assert.equal(widget("Available Models").value, router.model);
    const restored = await setup(undefined, storage);
    for (const [name, value] of Object.entries(router)) assert.equal(restored(name).value, value);
    restored.node.onConfigure({ properties: { llm_text_widget_names: ["provider", "timeout"] }, widgets_values: ["Anthropic", 120] });
    assert.equal(restored("timeout").value, 600);
    assert.equal(restored("model").value, "claude-test");
});
