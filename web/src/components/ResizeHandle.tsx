import { useCallback, useRef, useState, type PointerEvent } from "react";

export interface ResizeHandleProps {
  /** "horizontal" = drag up/down (splits rows). "vertical" = drag left/right
   *  (splits columns). Default "horizontal". */
  orientation?: "horizontal" | "vertical";
  /** Called with a signed pixel delta on the drag axis. For horizontal,
   *  positive = drag down. For vertical, positive = drag right. */
  onDelta: (deltaPx: number) => void;
  onStart?: () => void;
  onEnd?: () => void;
}

export default function ResizeHandle({
  orientation = "horizontal", onDelta, onStart, onEnd,
}: ResizeHandleProps) {
  const [dragging, setDragging] = useState(false);
  const last = useRef<number | null>(null);

  const handleDown = useCallback((e: PointerEvent<HTMLDivElement>) => {
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    last.current = orientation === "vertical" ? e.clientX : e.clientY;
    setDragging(true);
    onStart?.();
  }, [onStart, orientation]);

  const handleMove = useCallback((e: PointerEvent<HTMLDivElement>) => {
    if (last.current === null) return;
    const pos = orientation === "vertical" ? e.clientX : e.clientY;
    const delta = pos - last.current;
    last.current = pos;
    onDelta(delta);
  }, [onDelta, orientation]);

  const finish = useCallback((e: PointerEvent<HTMLDivElement>) => {
    if (last.current === null) return;
    last.current = null;
    setDragging(false);
    try {
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
    } catch { /* ignore */ }
    onEnd?.();
  }, [onEnd]);

  return (
    <div
      className={`split-handle split-${orientation} ${dragging ? "is-dragging" : ""}`}
      role="separator"
      aria-orientation={orientation}
      onPointerDown={handleDown}
      onPointerMove={handleMove}
      onPointerUp={finish}
      onPointerCancel={finish}
      title="Drag to resize"
    />
  );
}
