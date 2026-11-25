export function createRequestSequence() {
  let sequence = 0
  return {
    next: () => ++sequence,
    current: () => sequence,
    isCurrent: (candidate: number) => candidate === sequence,
  }
}
