import { describe, expect, it } from "vitest"

import { DEEPSEEK_STAGES, isSecretSettingKey } from "./settings-schema"

describe("settings schema", () => {
  it("exports stable DeepSeek stage ids", () => {
    expect(DEEPSEEK_STAGES).toEqual(["analyze", "polish", "summary", "mindmap"])
  })

  it("identifies secret runtime setting keys", () => {
    expect(isSecretSettingKey("deepseek_api_key")).toBe(true)
    expect(isSecretSettingKey("deepseek_summary_model")).toBe(false)
  })
})
