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
      <button
        className="bg-btn text-white rounded px-3 py-1 text-sm cursor-pointer hover:bg-btn-hover"
        onClick={handleCopy}
        title="Copy to clipboard"
      >
        {copied ? "\u2713 Copied" : label}
      </button>
    );
  }

  return (
    <button
      className="bg-transparent border-none cursor-pointer text-sm p-0.5 opacity-50 hover:opacity-100 shrink-0"
      onClick={handleCopy}
      title="Copy to clipboard"
    >
      {copied ? "\u2713" : "\uD83D\uDCCB"}
    </button>
  );
}
