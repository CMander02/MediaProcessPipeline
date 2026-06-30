import { describe, expect, it } from "vitest"

import {
  createServiceModelRecord,
  getCapabilitiesForModelType,
  getEndpointPathForModelType,
  getModelOptions,
  getModelTypeFromCapabilities,
} from "./settings-model-registry"

describe("getModelOptions", () => {
  it("filters models by provider, capability, and stage", () => {
    const options = getModelOptions({
      provider: "deepseek",
      capability: "thinking",
      stage: "summary",
    })

    expect(options.map((option) => option.value)).toEqual(["deepseek-v4-pro"])
  })

  it("keeps free-text model input available after filtering", () => {
    const options = getModelOptions({
      provider: "openai",
      capability: "chat",
      stage: "summary",
      query: "gpt-custom-internal",
    })

    expect(options).toContainEqual({
      kind: "free-text",
      value: "gpt-custom-internal",
      label: "gpt-custom-internal",
      provider: "openai",
    })
  })

  it("maps model types to capabilities and endpoint paths", () => {
    expect(getCapabilitiesForModelType("llm")).toEqual(["chat"])
    expect(getCapabilitiesForModelType("vlm")).toEqual(["chat", "vision"])
    expect(getCapabilitiesForModelType("embedding")).toEqual(["embedding"])
    expect(getCapabilitiesForModelType("rerank")).toEqual(["rerank"])
    expect(getEndpointPathForModelType("llm")).toBe("/chat/completions")
    expect(getEndpointPathForModelType("embedding")).toBe("/embeddings")
    expect(getEndpointPathForModelType("rerank")).toBe("/rerank")
    expect(getModelTypeFromCapabilities(["chat", "vision"])).toBe("vlm")
  })

  it("creates typed service model records for provider model lists", () => {
    expect(createServiceModelRecord({
      connectionId: "siliconflow-asr",
      modelId: "BAAI/bge-reranker-v2-m3",
      modelType: "rerank",
    })).toMatchObject({
      id: "siliconflow-asr:baai-bge-reranker-v2-m3",
      connection_id: "siliconflow-asr",
      model_id: "BAAI/bge-reranker-v2-m3",
      display_name: "BAAI/bge-reranker-v2-m3",
      model_type: "rerank",
      capabilities: ["rerank"],
      endpoint_path: "/rerank",
      enabled: true,
    })
  })
})
