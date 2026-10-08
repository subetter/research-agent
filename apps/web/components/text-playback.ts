/** Pace visible text independently of network chunk sizes. */
export function createTextPlayback(append: (id: string, text: string) => void) {
  const queue: {id: string; characters: string[]}[] = [];
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stopped = false;
  let resolveDrained: (() => void) | undefined;

  const tick = () => {
    timer = undefined;
    if (stopped) return;
    const item = queue[0];
    if (item) {
      const backlog = queue.reduce((n, entry) => n + entry.characters.length, 0);
      const count = backlog > 1000 ? 4 : backlog > 200 ? 2 : 1;
      append(item.id, item.characters.splice(0, count).join(""));
      if (!item.characters.length) queue.shift();
    }
    if (queue.length) timer = setTimeout(tick, 32);
    else {resolveDrained?.(); resolveDrained = undefined;}
  };

  return {
    enqueue(id: string, text: string) {
      if (stopped || !text) return;
      const last = queue[queue.length - 1];
      if (last?.id === id) last.characters.push(...Array.from(text));
      else queue.push({id, characters: Array.from(text)});
      if (!timer) timer = setTimeout(tick, 32);
    },
    drain() {
      if (stopped || !queue.length) return Promise.resolve();
      return new Promise<void>(resolve => {resolveDrained = resolve;});
    },
    cancel() {
      stopped = true;
      clearTimeout(timer);
      queue.length = 0;
      resolveDrained?.();
      resolveDrained = undefined;
    },
  };
}
