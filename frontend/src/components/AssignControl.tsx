import { useEffect, useState } from "react";
import { api } from "../api";
import { useApp } from "../state";
import type { Obligation, StaffUser } from "../types";

let cachedUsers: Promise<StaffUser[]> | null = null;
const loadUsers = () => (cachedUsers ??= api.users().then((r) => r.users).catch(() => ((cachedUsers = null), [])));

/** Who on the care team is chasing this follow-up (the owner is the outside party). */
export function AssignControl({ obligation, onChanged }: { obligation: Obligation; onChanged?: () => void }) {
  const { notify, refresh } = useApp();
  const [editing, setEditing] = useState(false);
  const [users, setUsers] = useState<StaffUser[]>([]);
  const clinicalOnly = obligation.category === "medication_reconciliation";

  useEffect(() => {
    if (editing) void loadUsers().then(setUsers);
  }, [editing]);

  const choose = async (userId: string) => {
    setEditing(false);
    try {
      const result = await api.assign(obligation.id, userId || null);
      notify(result.obligation.assignee ? `Assigned to ${result.obligation.assignee.name}.` : "Unassigned.");
      onChanged?.();
      await refresh();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not assign.");
    }
  };

  if (editing) {
    return (
      <select
        className="assign-select"
        aria-label="Assign to"
        autoFocus
        defaultValue={obligation.assignee?.id || ""}
        onChange={(event) => void choose(event.target.value)}
        onBlur={() => setEditing(false)}
      >
        <option value="">Nobody</option>
        {users
          .filter((u) => !clinicalOnly || u.role === "clinician")
          .map((u) => (
            <option key={u.id} value={u.id}>
              {u.name} ({u.role})
            </option>
          ))}
      </select>
    );
  }
  return (
    <button type="button" className="ob-more assign-button" onClick={() => setEditing(true)} title="Who on the team is chasing this">
      {obligation.assignee ? `Reassign` : "Assign to someone"}
    </button>
  );
}
