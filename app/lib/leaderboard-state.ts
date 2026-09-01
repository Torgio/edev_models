export type Leader = { model: string; observations: number; mae: number; updated_at: string };

/** Never substitute demo rows or show an earlier request's leader. */
export function currentLeaders(
  leaders: Leader[], status: string, loadedDay: string | undefined, selectedDay: string,
): Leader[] {
  return status === 'live' && loadedDay === selectedDay ? leaders : [];
}
