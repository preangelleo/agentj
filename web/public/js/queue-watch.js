// P481: a queue is suspect only after three minutes AND three minutes without earlier-turn output.
// A phone reload starts a fresh observation window; no claim about unobserved time.
export class QueueWatch {
  constructor(){ this.reset(); }
  reset(){ this.key = ''; this.output = ''; this.since = 0; this.quiet = 0; }
  check(queue, turns, now = Date.now()){
    if (!queue.length){ this.reset(); return false; }
    const first = queue[0], key = String(first.turn);
    const output = JSON.stringify(turns.filter(p => p.id < first.turn).map(p => [p.id, p.reply]));
    if (key !== this.key){ this.key = key; this.since = first.at || now; this.quiet = now; this.output = output; }
    if (output !== this.output){ this.output = output; this.quiet = now; }
    return now - this.since >= 180000 && now - this.quiet >= 180000;
  }
}
