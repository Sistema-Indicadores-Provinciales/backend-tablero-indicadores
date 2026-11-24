"""Source-order heap selection for imported charts with non-stable tied ranks.

Verified against exported and rendered Looker results. Only the order changes;
all remaining categories are returned for aggregation into Other by the caller.
"""
from functools import cmp_to_key


def source_heap_order(items, compare, limit=None):
    capacity = min(len(items), limit + 1) if limit else len(items)
    heap = []

    def worse(a, b):
        return compare(items[a], items[b]) > 0

    def push(index):
        heap.append(index)
        child = len(heap) - 1
        while child:
            parent = (child - 1) // 2
            if not worse(heap[child], heap[parent]):
                break
            heap[child], heap[parent] = heap[parent], heap[child]
            child = parent

    def pop():
        root = heap[0]
        last = heap.pop()
        if not heap:
            return root
        heap[0] = last
        parent = 0
        while 2 * parent + 1 < len(heap):
            child = 2 * parent + 1
            # Equal children choose the left branch. Equal parents sink.
            if child + 1 < len(heap) and worse(heap[child + 1], heap[child]):
                child += 1
            if worse(heap[parent], heap[child]):
                break
            heap[parent], heap[child] = heap[child], heap[parent]
            parent = child
        return root

    for index in range(len(items)):
        if len(heap) < capacity:
            push(index)
        elif worse(heap[0], index):
            # Removing before inserting matters for ties. Replacing the root
            # directly produces different members in the displayed categories.
            pop()
            push(index)
    selected = []
    while heap:
        selected.append(pop())
    selected.reverse()
    included = set(selected)
    remainder = [item for index, item in enumerate(items) if index not in included]
    return [items[index] for index in selected] + sorted(remainder, key=cmp_to_key(compare))
