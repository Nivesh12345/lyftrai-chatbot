import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

const API_URL = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/$/, "");

// A new session starts on every page load.
const newSessionId = () =>
  "s-" + (globalThis.crypto?.randomUUID?.().slice(0, 8) ?? Math.random().toString(36).slice(2, 10));

function readableError(status, data) {
  if (status === 503) {
    return "All AI providers are unavailable right now. Wait a moment and send your message again.";
  }
  if (status === 422) {
    const first = Array.isArray(data?.detail) ? data.detail[0] : null;
    const field = first?.loc?.[first.loc.length - 1];
    if (field === "message") {
      return "Your message must be between 1 and 4000 characters. Edit it and send again.";
    }
    return "The request was not valid. Check your message and try again.";
  }
  if (typeof data?.detail === "string") return data.detail;
  return `The server returned an unexpected error (${status}). Try again.`;
}

function normalizeMarkdown(markdown) {
  const lines = String(markdown ?? "").replace(/\r\n?/g, "\n").split("\n");
  const output = [];
  let fence = null;

  for (const original of lines) {
    const fenceMatch = original.match(/^\s*(`{3,}|~{3,})([^\s`]*)?\s*$/);

    if (fenceMatch) {
      const marker = fenceMatch[1][0];
      if (fence) {
        if (marker === fence.marker) {
          output.push(`${fence.marker}${fence.marker}${fence.marker}`);
          fence = null;
        }
      } else {
        const language = (fenceMatch[2] || "").trim();
        output.push(`${marker}${marker}${marker}${language ? ` ${language}` : ""}`);
        fence = { marker, language };
      }
      continue;
    }

    if (fence) {
      const trim = original.trim();
      const plainFenceProse =
        !fence.language &&
        (/^#{1,6}\s/.test(trim) ||
          /^[-*+]\s/.test(trim) ||
          /^\d+[.)]\s/.test(trim) ||
          /\*\*[^*]+\*\*/.test(trim) ||
          /[.!?]$/.test(trim));
      const namedFenceProse =
        Boolean(fence.language) &&
        !/^\s+/.test(original) &&
        (/^#{1,6}\s/.test(original) ||
          /^[-*+]\s/.test(original) ||
          /^\d+[.)]\s/.test(original) ||
          /^[A-Z][A-Za-z0-9 -]{3,}:/.test(original));

      if ((plainFenceProse || namedFenceProse) && trim) {
        output.push(`${fence.marker}${fence.marker}${fence.marker}`);
        fence = null;
      } else {
        output.push(original);
        continue;
      }
    }

    output.push(original);
  }

  if (fence) {
    output.push(`${fence.marker}${fence.marker}${fence.marker}`);
  }

  return output.join("\n");
}

function Metadata({ meta }) {
  const isCache = meta.resolution_source === "LLM_CACHE";
  return (
    <dl className="meta">
      <div>
        <dt>Source</dt>
        <dd>
          <span data-testid="meta-source" className={`badge ${isCache ? "cache" : "live"}`}>
            {meta.resolution_source}
          </span>
        </dd>
      </div>
      <div>
        <dt>Model</dt>
        <dd data-testid="meta-model">{meta.model}</dd>
      </div>
      <div>
        <dt>Latency</dt>
        <dd data-testid="meta-latency">{meta.latency_ms} ms</dd>
      </div>
      <div>
        <dt>Input tokens</dt>
        <dd data-testid="meta-input-tokens">{meta.input_tokens}</dd>
      </div>
      <div>
        <dt>Output tokens</dt>
        <dd data-testid="meta-output-tokens">{meta.output_tokens}</dd>
      </div>
      <div>
        <dt>Key</dt>
        <dd>{meta.key_id ?? "none (cache)"}</dd>
      </div>
      <div>
        <dt>Fallbacks</dt>
        <dd>{meta.fallback_attempts}</dd>
      </div>
    </dl>
  );
}

export default function App() {
  const [sessionId] = useState(newSessionId);
  const [items, setItems] = useState([]);
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [loading, setLoading] = useState(false);
  const [systemOpen, setSystemOpen] = useState(false);
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [items, loading]);

  async function send() {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    setItems((prev) => [...prev, { kind: "user", text }]);
    setLoading(true);
    try {
      const body = { session_id: sessionId, message: text };
      if (systemPrompt.trim()) body.system_prompt = systemPrompt.trim();
      const res = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      let data = null;
      try {
        data = await res.json();
      } catch {
        /* non-JSON body: handled below */
      }
      if (!res.ok) {
        setItems((prev) => [...prev, { kind: "error", text: readableError(res.status, data) }]);
      } else {
        setItems((prev) => [...prev, { kind: "assistant", text: data.reply, meta: data.metadata }]);
      }
    } catch {
      setItems((prev) => [
        ...prev,
        { kind: "error", text: "Cannot reach the server. Check your connection and try again." },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="app">
      <header className="top">
        <div>
          <p className="eyebrow">LLM Workspace</p>
          <h1>Chat</h1>
        </div>
        <div className="top-actions">
          <span className="session">Session {sessionId}</span>
          <button
            type="button"
            className="settings-button"
            aria-expanded={systemOpen}
            onClick={() => setSystemOpen((open) => !open)}
          >
            System prompt
          </button>
        </div>
      </header>

      {systemOpen && (
        <section className="system">
          <label htmlFor="system-prompt">System prompt for this session</label>
          <textarea
            id="system-prompt"
            data-testid="system-prompt-input"
            rows={2}
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="You are a helpful assistant."
          />
        </section>
      )}

      <main className="thread" aria-live="polite">
        {items.length === 0 && !loading && (
          <div className="empty">
            <div className="empty-mark" aria-hidden="true">AI</div>
            <h2>How can I help?</h2>
            <p>Ask a question, request a summary, or start a conversation.</p>
            <div className="suggestions">
              {["Explain this concept simply", "Give me three ideas", "Write a concise plan"].map((suggestion) => (
                <button key={suggestion} type="button" onClick={() => setInput(suggestion)}>
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}
        {items.map((item, i) => {
          if (item.kind === "user") {
            return (
              <div key={i} className="row user">
                <div className="bubble">{item.text}</div>
              </div>
            );
          }
          if (item.kind === "error") {
            return (
              <div key={i} className="row">
                <div className="bubble error" role="alert" data-testid="error-message">
                  {item.text}
                </div>
              </div>
            );
          }
          return (
            <div key={i} className="row">
              <div className="bubble assistant" data-testid="assistant-message">
                <div className="reply markdown">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm, remarkMath]}
                    rehypePlugins={[rehypeKatex]}
                    components={{
                      a: ({ node: _, ...props }) => (
                        <a {...props} target="_blank" rel="noreferrer" />
                      ),
                    }}
                  >
                    {normalizeMarkdown(item.text)}
                  </ReactMarkdown>
                </div>
                <Metadata meta={item.meta} />
              </div>
            </div>
          );
        })}
        {loading && (
          <div className="row">
            <div className="bubble pending">Waiting for the model…</div>
          </div>
        )}
        <div ref={endRef} />
      </main>

      <footer className="composer">
        <textarea
          data-testid="chat-input"
          rows={2}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Write a message. Enter sends, Shift+Enter adds a line."
          aria-label="Message"
        />
        <button data-testid="send-button" onClick={send} disabled={loading}>
          Send
        </button>
      </footer>
    </div>
  );
}
