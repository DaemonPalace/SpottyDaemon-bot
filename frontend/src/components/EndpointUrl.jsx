import { useState } from "react";

/** Hosted only: the Interactions Endpoint URL to paste into the Discord
 * developer portal, read-only with a copy button. */
export default function EndpointUrl({ url, children }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      // No clipboard access (insecure context, denied): the field is selectable.
    }
  }

  return (
    <label>
      Interactions Endpoint URL
      <span className="hint" style={{ display: "block" }}>
        {children}
      </span>
      <span style={{ display: "flex", gap: "0.5rem" }}>
        <input readOnly value={url} onFocus={(e) => e.target.select()} style={{ flex: 1 }} />
        <button type="button" className="ghost" onClick={copy}>
          {copied ? "Copied" : "Copy"}
        </button>
      </span>
    </label>
  );
}
