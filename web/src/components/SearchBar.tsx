import { useEffect, useRef, type ChangeEvent } from "react";

export interface SearchBarProps {
  value: string;
  onChange: (v: string) => void;
  busy?: boolean;
  placeholder?: string;
}

export default function SearchBar({
  value, onChange, busy = false,
  placeholder = "Search across all transcripts…",
}: SearchBarProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  // Ctrl/Cmd + K to focus.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
      }
      if (e.key === "Escape" && document.activeElement === inputRef.current) {
        inputRef.current?.blur();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className={`searchbar ${busy ? "is-busy" : ""}`}>
      <span className="searchbar-icon" aria-hidden="true">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" />
        </svg>
      </span>
      <input
        ref={inputRef}
        type="text"
        value={value}
        onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(e.target.value)}
        placeholder={placeholder}
        spellCheck={false}
        autoCorrect="off"
        autoCapitalize="off"
      />
      {value && (
        <button
          type="button"
          className="searchbar-clear"
          onClick={() => { onChange(""); inputRef.current?.focus(); }}
          aria-label="Clear"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <path d="M6 6l12 12M6 18L18 6" />
          </svg>
        </button>
      )}
      {!value && <span className="searchbar-kbd">⌃ K</span>}
    </div>
  );
}
