import { useState, type FormEvent } from "react";

interface Props {
  open: boolean;
  onClose: () => void;
  onSubmit: (name: string, description: string) => Promise<void> | void;
}

export default function NewProjectModal({ open, onClose, onSubmit }: Props) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit(name.trim(), desc.trim());
      setName("");
      setDesc("");
      onClose();
    } catch (err) {
      setError(String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>New project</h2>
        <form onSubmit={submit}>
          <label>
            Name
            <input autoFocus value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label>
            Description <span className="hint">(optional)</span>
            <textarea rows={3} value={desc} onChange={(e) => setDesc(e.target.value)} />
          </label>
          {error && <div className="form-error">{error}</div>}
          <div className="modal-actions">
            <button type="button" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" disabled={submitting || !name.trim()}>
              Create
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
