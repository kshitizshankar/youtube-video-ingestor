import { useEffect, type ReactNode } from "react";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmDialog({
  open, title, body,
  confirmLabel = "confirm", cancelLabel = "cancel",
  destructive = false, busy = false,
  onConfirm, onCancel,
}: ConfirmDialogProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      className="confirm-backdrop"
      onClick={(e) => { if (e.target === e.currentTarget) onCancel(); }}
    >
      <div className="confirm-card">
        <h3>{title}</h3>
        <div className="confirm-body">{body}</div>
        <div className="confirm-actions">
          <button className="btn" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button
            className={destructive ? "btn btn-destructive" : "btn btn-primary"}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "working..." : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
