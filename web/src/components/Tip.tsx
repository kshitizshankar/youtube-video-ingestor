import * as T from "@radix-ui/react-tooltip";
import type { ReactElement, ReactNode } from "react";

export interface TipProps {
  /** Tooltip content — can be any node. Kept compact in practice. */
  content: ReactNode;
  side?: "top" | "right" | "bottom" | "left";
  align?: "start" | "center" | "end";
  delay?: number;
  children: ReactElement;
}

/** Thin wrapper around Radix Tooltip. Portals to document.body so it's
 *  never clipped by a modal's overflow:hidden. Styled via .tip-content /
 *  .tip-arrow in index.css. */
export default function Tip({
  content, side = "top", align = "center", delay = 180, children,
}: TipProps) {
  if (!content) return children;
  return (
    <T.Root delayDuration={delay}>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal>
        <T.Content
          className="tip-content"
          side={side}
          align={align}
          sideOffset={8}
          collisionPadding={10}
        >
          {content}
          <T.Arrow className="tip-arrow" width={11} height={5} />
        </T.Content>
      </T.Portal>
    </T.Root>
  );
}
