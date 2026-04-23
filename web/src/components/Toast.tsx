import { useEffect } from "react";

export type ToastKind = "info" | "success" | "error";

interface Props {
  open: boolean;
  kind?: ToastKind;
  message: string;
  onClose: () => void;
  /** Milliseconds before auto-dismissing. Pass 0 to disable auto-hide. */
  autoHideMs?: number;
}

/** Minimal toast — fixed bottom-right, auto-hides, driven by a parent state
 *  object. Kind maps to accent/ok/warn tinting defined in index.css. */
export default function Toast({
  open,
  kind = "info",
  message,
  onClose,
  autoHideMs = 5000,
}: Props) {
  useEffect(() => {
    if (!open) return;
    if (!autoHideMs) return;
    const id = window.setTimeout(onClose, autoHideMs);
    return () => window.clearTimeout(id);
  }, [open, onClose, autoHideMs]);

  if (!open) return null;
  return (
    <div className={`toast toast-${kind}`} role="status" aria-live="polite">
      <span className="toast-message">{message}</span>
      <button
        type="button"
        className="toast-dismiss"
        aria-label="Dismiss notification"
        onClick={onClose}
      >
        ×
      </button>
    </div>
  );
}
