/**
 * Generic right-side drawer. Click outside or hit ESC to close.
 */
import { ReactNode, useEffect } from "react";
import { X } from "lucide-react";

export function SidePanel({
  open,
  onClose,
  title,
  width = "w-[600px]",
  children,
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  width?: string;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const handle = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [open, onClose]);

  return (
    <>
      {/* Backdrop */}
      <div
        className={`fixed inset-0 z-40 bg-black/40 transition-opacity ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
        onClick={onClose}
      />
      {/* Drawer */}
      <aside
        className={`fixed inset-y-0 right-0 z-50 ${width} max-w-[95vw] transform border-l border-border bg-card shadow-2xl transition-transform ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
      >
        <header className="flex h-14 items-center justify-between border-b border-border px-5">
          <div className="min-w-0 truncate">{title}</div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-muted hover:bg-bg hover:text-foreground"
            aria-label="close"
          >
            <X className="size-4" />
          </button>
        </header>
        <div className="h-[calc(100vh-3.5rem)] overflow-y-auto p-5">{children}</div>
      </aside>
    </>
  );
}
