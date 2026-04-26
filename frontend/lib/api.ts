export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

export type DocumentRecord = {
  id: string;
  filename: string;
  mime: string;
  size_bytes: number;
  pages: number | null;
  uploaded_at: string | null;
  status: "ingesting" | "ready" | "failed";
  file_url?: string;
  html_url?: string;
};

export type Snippet = {
  id: string;
  parent_id: string;
  text: string;
  score: number;
  page_start: number | null;
  page_end: number | null;
  bboxes: Array<{ page: number; x0: number; y0: number; x1: number; y1: number }>;
  html_anchor: string | null;
};

export async function listDocuments(): Promise<DocumentRecord[]> {
  const r = await fetch(`${API_BASE}/documents`);
  if (!r.ok) throw new Error(`list failed: ${r.status}`);
  return r.json();
}

export async function getDocument(id: string): Promise<DocumentRecord> {
  const r = await fetch(`${API_BASE}/documents/${id}`);
  if (!r.ok) throw new Error(`get failed: ${r.status}`);
  return r.json();
}

export async function deleteDocument(id: string): Promise<void> {
  const r = await fetch(`${API_BASE}/documents/${id}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`delete failed: ${r.status}`);
}

export type SseHandlers = {
  onStage?: (payload: any) => void;
  onSnippets?: (items: Snippet[]) => void;
  onToken?: (tok: string) => void;
  onDone?: (payload: any) => void;
  onError?: (msg: string) => void;
};

/** Parse a ReadableStream of SSE bytes and invoke handlers. */
export async function consumeSSE(
  response: Response,
  handlers: SseHandlers,
): Promise<void> {
  if (!response.ok || !response.body) {
    handlers.onError?.(`HTTP ${response.status}`);
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const dispatch = (event: string, dataRaw: string) => {
    let data: any = dataRaw;
    try {
      data = JSON.parse(dataRaw);
    } catch {
      // token events are plain strings — leave as-is
    }
    switch (event) {
      case "stage":
        handlers.onStage?.(data);
        break;
      case "snippets":
        handlers.onSnippets?.(data);
        break;
      case "token":
        handlers.onToken?.(typeof data === "string" ? data : dataRaw);
        break;
      case "done":
        handlers.onDone?.(data);
        break;
      case "error":
        handlers.onError?.(typeof data === "string" ? data : data?.message ?? "error");
        break;
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const raw = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const lines = raw.split("\n");
      let event = "message";
      const dataParts: string[] = [];
      for (const line of lines) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) {
          // SSE spec: strip exactly one optional leading space, not all whitespace.
          const v = line.slice(5);
          dataParts.push(v.startsWith(" ") ? v.slice(1) : v);
        }
      }
      dispatch(event, dataParts.join("\n"));
    }
  }
}

export async function uploadDocument(file: File): Promise<DocumentRecord> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`${API_BASE}/documents`, {
    method: "POST",
    body: fd,
  });
  if (!r.ok) throw new Error(`upload failed: ${r.status}`);
  return r.json();
}

export async function runQuery(
  docId: string,
  query: string,
  topK: number,
  handlers: SseHandlers,
) {
  const r = await fetch(`${API_BASE}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doc_id: docId, query, top_k: topK }),
  });
  return consumeSSE(r, handlers);
}
