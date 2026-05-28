/**
 * Generic "Export CSV" button. Caller supplies rows + column mapping.
 */
import { Download } from "lucide-react";
import { useTranslation } from "react-i18next";

import { downloadCSV, tsFilename, type Column } from "@/lib/csv";

export function ExportCsvButton<T>({
  rows,
  cols,
  filenamePrefix,
  disabled,
}: {
  rows: T[];
  cols: Column<T>[];
  filenamePrefix: string;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      disabled={disabled || rows.length === 0}
      onClick={() => downloadCSV(tsFilename(filenamePrefix), rows, cols)}
      className="inline-flex items-center gap-1.5 rounded border border-border bg-card px-2.5 py-1 text-xs text-muted transition-colors hover:bg-bg hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
      title={t("common.export")}
    >
      <Download className="size-3.5" />
      CSV · {rows.length}
    </button>
  );
}
