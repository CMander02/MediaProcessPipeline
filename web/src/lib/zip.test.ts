import { describe, expect, it } from "vitest"

import { createZip, safeFileName } from "./zip"

function readUint32(bytes: Uint8Array, offset: number) {
  return new DataView(bytes.buffer, bytes.byteOffset).getUint32(offset, true)
}

function readUint16(bytes: Uint8Array, offset: number) {
  return new DataView(bytes.buffer, bytes.byteOffset).getUint16(offset, true)
}

describe("createZip", () => {
  it("writes stored entries with UTF-8 names and a valid central directory", () => {
    const zip = createZip([
      { name: "访谈/summary.md", content: "# 摘要\n" },
      { name: "访谈/transcript.srt", content: "1\n00:00:00,000 --> 00:00:01,000\nhi" },
    ], new Date(2026, 8, 27, 10, 30, 0))

    expect(readUint32(zip, 0)).toBe(0x04034b50)
    const end = zip.length - 22
    expect(readUint32(zip, end)).toBe(0x06054b50)
    expect(readUint16(zip, end + 10)).toBe(2)

    const centralOffset = readUint32(zip, end + 16)
    expect(readUint32(zip, centralOffset)).toBe(0x02014b50)
    const nameLength = readUint16(zip, centralOffset + 28)
    const name = new TextDecoder().decode(zip.slice(centralOffset + 46, centralOffset + 46 + nameLength))
    expect(name).toBe("访谈/summary.md")

    // Known CRC-32 of the UTF-8 bytes of "# 摘要\n"
    const data = new TextEncoder().encode("# 摘要\n")
    expect(readUint32(zip, 18)).toBe(data.length)
    expect(readUint32(zip, 14)).toBe(crc(data))
  })
})

describe("safeFileName", () => {
  it("replaces characters Windows rejects and trims trailing dots", () => {
    expect(safeFileName('a/b:c*d?"e<f>g|h.')).toBe("a_b_c_d__e_f_g_h")
    expect(safeFileName("   ")).toBe("export")
  })
})

// Independent bitwise CRC-32 to check the table-driven one.
function crc(data: Uint8Array) {
  let value = 0xffffffff
  for (const byte of data) {
    value ^= byte
    for (let k = 0; k < 8; k += 1) value = value & 1 ? (value >>> 1) ^ 0xedb88320 : value >>> 1
  }
  return (value ^ 0xffffffff) >>> 0
}
