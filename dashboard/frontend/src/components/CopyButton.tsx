import { useState } from "react";

export function CopyButton({ text, label }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  if (label) {
    return (
      <button className="copy-prompt-btn" onClick={handleCopy} title="Copy to clipboard">
        {copied ? "\u2713 Copied" : label}
      </button>
    );
  }

  return (
    <button className="copy-btn" onClick={handleCopy} title="Copy to clipboard">
      {copied ? "\u2713" : "\uD83D\uDCCB"}
    </button>
  );
}
