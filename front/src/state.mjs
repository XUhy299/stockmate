export function isBusy(task) {
  return task?.status === 'queued' || task?.status === 'running'
}

export function nearBottom(scrollHeight, scrollTop, clientHeight) {
  return scrollHeight - scrollTop - clientHeight < 140
}

export function displayValue(value) {
  return value === null || value === undefined || value === '' ? '暂无' : String(value)
}

export function taskEvents(events, task) {
  return events.filter(event => event.task_id === task?.task_id)
}
