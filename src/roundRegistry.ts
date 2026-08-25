import type { IVybeRounds } from './tokens';

/**
 * Late binding between the cell executor and the round machine.
 *
 * These two cannot be wired through the plugin graph: rounds needs
 * INotebookTracker, the tracker needs INotebookCellExecutor, and the executor
 * would need rounds -- a cycle, which Lumino rejects at registration and which
 * takes the whole application down with it (docs/13 R1).
 *
 * So the executor never declares rounds as a dependency. The rounds plugin
 * registers itself here on activation, and the executor looks it up lazily
 * inside runCell -- which it already had to do, since nothing may be resolved
 * during its own activate.
 */
let rounds: IVybeRounds | null = null;

export function setRounds(value: IVybeRounds): void {
  rounds = value;
}

export function getRounds(): IVybeRounds | null {
  return rounds;
}
