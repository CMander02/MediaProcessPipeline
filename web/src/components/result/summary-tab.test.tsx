/** @vitest-environment jsdom */

import { cleanup, render, screen, within } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, describe, expect, it } from "vitest"

import { SummaryTab } from "./summary-tab"

afterEach(cleanup)

describe("SummaryTab", () => {
  it("opens with the summary itself, then key points, then the outline, all in Chinese headings", () => {
    const { container } = render(
      <SummaryTab
        content={[
          "---",
          'title: "示例"',
          "---",
          "",
          "# 示例",
          "",
          "## Summary",
          "摘要正文。",
          "",
          "### Key Facts",
          "- 第一条事实",
          "- 第二条事实",
          "",
          "## 内容脉络",
          "",
          "### 第一章",
          "",
          "第一章的内容。",
        ].join("\n")}
      />,
    )

    const headings = screen.getAllByRole("heading").map((heading) => heading.textContent)
    expect(headings).toEqual(["核心要点", "内容脉络", "第一章"])
    const lead = screen.getByText("摘要正文。")
    const facts = screen.getByRole("heading", { name: "核心要点" })
    expect(lead.compareDocumentPosition(facts) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(within(screen.getByRole("list")).getAllByRole("listitem")).toHaveLength(2)
    expect(container.querySelector("hr")).toBeNull()
    expect(container.querySelector(".border-t")).toBeNull()
  })
})
