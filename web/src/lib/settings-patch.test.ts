import { describe, expect, it } from "vitest"

import {
  createCustomProfileMirrorPatch,
  createSettingsPatch,
  expandSettingsPatch,
  isMaskedSecret,
  type CustomLLMProfile,
  type RuntimeSettings,
} from "./settings-patch"

describe("expandSettingsPatch", () => {
  it("expands supported dot paths into runtime settings keys", () => {
    expect(expandSettingsPatch({
      "llm.provider": "deepseek",
      "deepseek.summary.model": "deepseek-v4-pro",
      "deepseek.summary.thinking": "enabled",
      "deepseek.summary.effort": "max",
      "asr.provider": "siliconflow",
      openai_model: "gpt-4o-mini",
    })).toEqual({
      llm_provider: "deepseek",
      deepseek_summary_model: "deepseek-v4-pro",
      deepseek_summary_thinking: "enabled",
      deepseek_summary_effort: "max",
      asr_provider: "siliconflow",
      openai_model: "gpt-4o-mini",
    })
  })
})

describe("createSettingsPatch", () => {
  it("keeps masked secrets unchanged, clears empty secrets, and replaces new secrets", () => {
    expect(isMaskedSecret("***...cdef")).toBe(true)
    expect(createSettingsPatch({ api_token: "***...cdef" })).toEqual({})
    expect(createSettingsPatch({ api_token: "" })).toEqual({ api_token: "" })
    expect(createSettingsPatch({ api_token: "new-token" })).toEqual({ api_token: "new-token" })
  })
})

describe("createCustomProfileMirrorPatch", () => {
  it("mirrors the active custom profile into legacy custom fields", () => {
    const current: RuntimeSettings = {
      custom_active_profile_id: "work",
      custom_llm_profiles: [
        { id: "work", name: "Work", api_base: "https://work.example/v1", model: "work-model", api_key: "***...work" },
        { id: "lab", name: "Lab", api_base: "https://lab.example/v1", model: "lab-model", api_key: "lab-key" },
      ],
    }
    const profiles: CustomLLMProfile[] = [
      { id: "work", name: "Work", api_base: "https://work.example/v1", model: "work-model", api_key: "***...work" },
      { id: "lab", name: "Lab", api_base: "https://lab.example/v1", model: "lab-model-2", api_key: "lab-key" },
    ]

    expect(createCustomProfileMirrorPatch(current, profiles, "lab")).toEqual({
      custom_llm_profiles: profiles,
      custom_active_profile_id: "lab",
      custom_name: "Lab",
      custom_api_base: "https://lab.example/v1",
      custom_model: "lab-model-2",
      custom_api_key: "lab-key",
    })
  })
})
