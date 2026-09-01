import { describe, expect, it } from "vitest";

import {
  citationLabel,
  hitsFromEvidenceContent,
  provenanceEntries,
} from "@/lib/knowledge-citation";

describe("hitsFromEvidenceContent", () => {
  it("maps hits arrays to provenance fields without inventing values", () => {
    const hits = hitsFromEvidenceContent({
      hits: [
        {
          chunk_id: "c1",
          title: "退款政策",
          source: "feishu",
          connector_name: "feishu-docs",
          external_id: "doccn1",
          revision_id: "rev-9",
          operation: "knowledge.search",
          version: 3,
        },
        { chunk_id: "c2" },
      ],
    });
    expect(hits).toHaveLength(2);
    expect(hits[0]).toMatchObject({
      chunk_id: "c1",
      title: "退款政策",
      connector_name: "feishu-docs",
      revision_id: "rev-9",
      version: "3",
    });
    expect(hits[1]?.chunk_id).toBe("c2");
    expect(hits[1]?.title).toBeNull();
  });

  it("falls back to top-level provenance and drops empty records", () => {
    const single = hitsFromEvidenceContent({ title: "发布说明", source: "confluence" });
    expect(single).toHaveLength(1);
    expect(single[0]?.title).toBe("发布说明");
    expect(hitsFromEvidenceContent({})).toEqual([]);
    expect(hitsFromEvidenceContent({ hits: "not-an-array" })).toEqual([]);
  });
});

describe("provenanceEntries", () => {
  it("omits missing or blank fields instead of fabricating them", () => {
    expect(provenanceEntries({})).toEqual([]);
    const entries = provenanceEntries({ source: " feishu ", version: 12, external_id: "" });
    expect(entries).toEqual([
      { label: "来源", value: "feishu" },
      { label: "版本", value: "v12" },
    ]);
  });
});

describe("citationLabel", () => {
  it("uses persisted title/source with explicit defaults", () => {
    expect(citationLabel({ title: "退款政策", source: "feishu" }, 2)).toBe("[2] feishu · 退款政策");
    expect(citationLabel({}, 1)).toBe("[1] knowledge · 授权文档");
  });
});
