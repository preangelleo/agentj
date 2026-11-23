// 0371: the one terminal line for a 0.30.47-0.30.65 host whose own updater cannot finish on Linux (see repair-actions.js).
// Two constants and nothing else: the card shows the line (repair-actions.js) and the terminal module runs exactly this
// line (termcommands.js fixedLine) — no caller can hand over another one.
// A subshell: the owner's own shell keeps its umask.
export const OLD_UPDATER_FIX = '( umask 077; chmod -R go-w ~/.local/state/agentj/install-artifacts/dependencies; agentj update apply --yes )';
// false = the card offers copy only. Jarvis turns it on after the run button was proven on a real 0.30.65 host with a real phone.
export const OLD_FIX_RUN = false;
