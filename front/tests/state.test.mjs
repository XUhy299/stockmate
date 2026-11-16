import test from 'node:test'
import assert from 'node:assert/strict'
import { isBusy, nearBottom, displayValue, taskEvents } from '../src/state.mjs'

test('only queued and running tasks block submit', () => {
  for (const status of ['queued', 'running']) assert.equal(isBusy({ status }), true)
  for (const status of ['completed', 'failed', 'interrupted']) assert.equal(isBusy({ status }), false)
  assert.equal(isBusy(null), false)
})
test('scroll follows only near the bottom', () => {
  assert.equal(nearBottom(1000, 500, 450), true)
  assert.equal(nearBottom(2000, 100, 450), false)
})
test('missing metrics remain unknown and zero remains zero', () => {
  assert.equal(displayValue(null), '暂无')
  assert.equal(displayValue(undefined), '暂无')
  assert.equal(displayValue(0), '0')
})
test('execution details are scoped to current task', () => {
  const events = [{ id: 1, task_id: 'a' }, { id: 2, task_id: 'b' }]
  assert.deepEqual(taskEvents(events, { task_id: 'b' }), [events[1]])
})
