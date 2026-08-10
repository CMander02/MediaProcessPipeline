/** @vitest-environment jsdom */

import { cleanup, render, screen, within } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, describe, expect, it } from "vitest"

import { SummaryTab } from "./summary-tab"

afterEach(cleanup)

describe("SummaryTab", () => {
  it("renders the narrative summary before key facts as a Markdown list without separators", () => {
    const { container } = render(
      <SummaryTab
        content={[
          "# 示例",
          "",
          "## Summary",
          "摘要正文。",
          "",
          "### Key Facts",
          "- 第一条事实",
          "- 第二条事实",
        ].join("\n")}
      />,
    )

    const summaryHeading = screen.getByRole("heading", { name: "Summary" })
    const factsHeading = screen.getByRole("heading", { name: "核心要点" })

    expect(
      summaryHeading.compareDocumentPosition(factsHeading) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(within(screen.getByRole("list")).getAllByRole("listitem")).toHaveLength(2)
    expect(container.querySelector("hr")).toBeNull()
    expect(container.querySelector(".border-t")).toBeNull()
  })
})
