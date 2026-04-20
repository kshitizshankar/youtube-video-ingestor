import { useCallback, useRef, useState, type PointerEvent } from "react";

export interface ResizeHandleProps {
  /** Called with a signed pixel delta (positive = drag down). */
  onDelta: (deltaPx: number) => void;
  onStart?: () => void;
  onEnd?: () => void;
}

export default function ResizeHandle({ onDelta, onStart, onEnd }: ResizeHandleProps) {
  const [dragging, setDragging] = useState(false);
  const lastY = useRef<number | null>(null);

  const handleDown = useCallback((e: PointerEvent<HTMLDivElement>) => {
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    lastY.current = e.clientY;
    setDragging(true);
    onStart?.();
  }, [onStart]);

  const handleMove = useCallback((e: PointerEvent<HTMLDivElement>) => {
    if (lastY.current === null) return;
    const delta = e.clientY - lastY.current;
    lastY.current = e.clientY;
    onDelta(delta);
  }, [onDelta]);

  const finish = useCallback((e: PointerEvent<HTMLDivElement>) => {
    if (lastY.current === null) return;
    lastY.current = null;
    setDragging(false);
    try {
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
    } catch { /* ignore */ }
    onEnd?.();
  }, [onEnd]);

  return (
    <div
      className={`split-handle ${dragging ? "is-dragging" : ""}`}
      role="separator"
      aria-orientation="horizontal"
      onPointerDown={handleDown}
      onPointerMove={handleMove}
      onPointerUp={finish}
      onPointerCancel={finish}
      title="Drag to resize"
    />
  );
}
