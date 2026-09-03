"use client";

import { ReactNode, useId } from "react";

/**
 * One labelled row in a `.fields` list.
 *
 * The visual pattern — <dt>label</dt><dd>control</dd> — has been the
 * dashboard's form row since Phase 5, but a <dt> is not a <label>:
 * VoiceOver and TalkBack announce the control as "edit text" with no
 * name, so a technician filing the service report by voice has no idea
 * which box they are in. Passing the control as a function hands it the
 * id the label points at — the one hook a screen reader needs — and the
 * row geometry does not change at all.
 *
 * Plain-node children render a value row (no control, so no <label>
 * element), which lets record pages switch a row between read and edit
 * without swapping wrappers.
 */
export function FieldRow({
  label,
  empty,
  children,
}: {
  label: ReactNode;
  /** Read mode with nothing to show — styled as absent, not as a mistake. */
  empty?: boolean;
  children: ReactNode | ((id: string) => ReactNode);
}) {
  const id = useId();
  const control = typeof children === "function";
  return (
    <div className="field-row">
      <dt>{control ? <label htmlFor={id}>{label}</label> : label}</dt>
      <dd data-empty={empty ? "true" : undefined}>
        {control ? children(id) : children}
      </dd>
    </div>
  );
}
