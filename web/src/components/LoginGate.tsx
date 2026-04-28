import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { authFetch, clearToken, setToken } from "../auth";

type Phase = "probing" | "open" | "locked" | "error";

export interface LoginGateProps {
  children: ReactNode;
}

/** Wraps the app with a login check. If the backend's auth is disabled, or
 *  the stored token validates, renders `children`. Otherwise shows a login
 *  form. */
export default function LoginGate({ children }: LoginGateProps) {
  const [phase, setPhase] = useState<Phase>("probing");
  const [err, setErr] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const probe = async () => {
    try {
      const r = await authFetch("/api/auth/check");
      if (r.ok) {
        setPhase("open");
        setErr(null);
      } else if (r.status === 401) {
        setPhase("locked");
      } else {
        setPhase("error");
        setErr(`Server returned ${r.status}`);
      }
    } catch (e) {
      setPhase("error");
      setErr(String(e));
    }
  };

  useEffect(() => { probe(); }, []);

  if (phase === "open") return <>{children}</>;
  if (phase === "probing") return <div className="gate-splash">Checking…</div>;

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const data = new FormData(form);
    const user = String(data.get("user") || "");
    const pass = String(data.get("pass") || "");
    if (!user || !pass) return;
    setSubmitting(true);
    setErr(null);
    setToken(user, pass);
    try {
      const r = await authFetch("/api/auth/check");
      if (r.ok) {
        setPhase("open");
      } else {
        clearToken();
        setErr("Wrong username or password.");
      }
    } catch (e2) {
      setErr(String(e2));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="gate">
      <form className="gate-card" onSubmit={handleSubmit}>
        <div className="gate-mark" aria-label="vidan.">vidan</div>
        <h2>
          unlock<em>.</em>
        </h2>
        <p className="gate-sub">
          for humans who don&rsquo;t have 90 min. password-protected instance —
          enter your credentials to continue.
        </p>
        <label className="gate-field">
          <span>username</span>
          <input name="user" type="text" autoComplete="username" autoFocus spellCheck={false} />
        </label>
        <label className="gate-field">
          <span>password</span>
          <input name="pass" type="password" autoComplete="current-password" />
        </label>
        {err && <div className="gate-err">{err}</div>}
        <button type="submit" className="btn btn-primary gate-btn" disabled={submitting}>
          {submitting ? "checking…" : "unlock"}
        </button>
        {phase === "error" && !err && (
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => { clearToken(); probe(); }}
          >
            retry
          </button>
        )}
      </form>
    </div>
  );
}
