import { describe, expect, it } from 'vitest'
import { createRequestSequence } from './requestSequence'

describe('request sequence', () => {
  it('rejects a stale response after a newer request starts', () => {
    const sequence = createRequestSequence()
    const first = sequence.next()
    const second = sequence.next()

    expect(sequence.isCurrent(second)).toBe(true)
    expect(sequence.isCurrent(first)).toBe(false)
  })
})
