import { describe, expect, it } from "vitest"

import { getModelOptions } from "./settings-model-registry"

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
})
