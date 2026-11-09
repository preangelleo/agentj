// To-do presentation only: keep host ordering, filter before allocating row DOM.
export const TODO_BATCH = 10;
export const todoClosed = item => item.done === true || item.status === 'done' || item.status === 'cancelled';
export function todoItems(items, {owner = 'all', hideDone = true, matches = () => true} = {}) {
  return items.filter(item => (owner === 'all' || (item.owner === 'agent' ? 'agent' : 'owner') === owner) && (!hideDone || !todoClosed(item)) && matches(item));
}
export function todoPage(items, limit = TODO_BATCH) {
  return items.slice(0, Math.max(TODO_BATCH, limit));
}
