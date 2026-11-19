/**
 * Shared look for native form controls (`<input>`, `<select>`, `<textarea>`).
 * Use it as the base class and add only layout (width) on top. Invalid
 * fields set `aria-invalid="true"` and get the destructive border.
 */
export const fieldClass =
  "h-9 rounded-md border border-input bg-background px-3 text-sm text-foreground transition-[border-color,box-shadow] outline-none placeholder:text-muted-foreground focus-visible:border-foreground/40 focus-visible:ring-[3px] focus-visible:ring-ring/25 disabled:cursor-not-allowed disabled:opacity-50 aria-[invalid=true]:border-destructive aria-[invalid=true]:ring-destructive/20";

/** Small uppercase-free label above a field. */
export const labelClass = "text-sm font-medium text-foreground";
