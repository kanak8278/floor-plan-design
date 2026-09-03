/**
 * Is the user typing into something?
 *
 * Global keyboard shortcuts and text entry share one window. The editor binds
 * single letters to tools (`v`, `w`, `d`, `t`) and `Space` to canvas panning,
 * so any handler on `window` has to stand down while a field has focus —
 * otherwise typing "Master Bedroom" switches to the measure tool halfway
 * through and never gets its space.
 *
 * That was a live bug before the chat pane existed: `FloorPlanCanvas.onKeyDown`
 * called `preventDefault()` on `Space` before it computed its own form-field
 * check, so the room-rename box in the properties panel could not accept a
 * space either. One helper, used by every global handler, is the only way this
 * stays fixed.
 */
export function isTypingTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  // A contenteditable region counts, and so does anything inside one.
  return typeof el.closest === 'function'
    ? !!el.closest('[contenteditable="true"], [contenteditable=""]')
    : false;
}

/**
 * Should a global shortcut run for this event?
 *
 * Escape is always allowed through: cancelling is the one thing a user must be
 * able to do from inside a field.
 */
export function shortcutsAllowed(e: KeyboardEvent): boolean {
  if (e.key === 'Escape') return true;
  return !isTypingTarget(e.target);
}
