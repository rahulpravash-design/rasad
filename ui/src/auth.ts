// Tiny token store for the two demo roles. localStorage may be unavailable; fall back to memory.
let memory: { token: string; role: string; user: string } | null = null;
const KEY = "rasad.session";

export function getSession() {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return JSON.parse(raw) as typeof memory;
  } catch {
    /* storage blocked */
  }
  return memory;
}

export function setSession(s: typeof memory) {
  memory = s;
  try {
    if (s) localStorage.setItem(KEY, JSON.stringify(s));
    else localStorage.removeItem(KEY);
  } catch {
    /* storage blocked */
  }
}
