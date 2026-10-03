import { describe, expect, it } from "vitest"

import { describeProxy, proxyMode } from "@/lib/proxy"

describe("proxy values", () => {
  it("reads empty as the system proxy and the backend's off words as no proxy", () => {
    expect(proxyMode("")).toBe("system")
    expect(proxyMode("  ")).toBe("system")
    for (const off of ["direct", "none", "OFF", "false", "0"]) expect(proxyMode(off)).toBe("none")
    expect(proxyMode("socks5://127.0.0.1:1080")).toBe("custom")
  })

  it("describes a value without its credentials", () => {
    expect(describeProxy("")).toBe("系统代理")
    expect(describeProxy("direct")).toBe("无代理")
    expect(describeProxy(" http://127.0.0.1:7897 ")).toBe("http://127.0.0.1:7897")
    expect(describeProxy("http://user:secret@proxy.lan:8080")).toBe("http://proxy.lan:8080")
  })
})
