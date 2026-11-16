const clearance = 14;
const turnCost = 12;

function segmentHitsBox(from, to, box) {
  if (Math.max(from.x, to.x) <= box.left || Math.min(from.x, to.x) >= box.right ||
      Math.max(from.y, to.y) <= box.top || Math.min(from.y, to.y) >= box.bottom) return false;
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const x1 = dx ? (box.left - from.x) / dx : -Infinity;
  const x2 = dx ? (box.right - from.x) / dx : Infinity;
  const y1 = dy ? (box.top - from.y) / dy : -Infinity;
  const y2 = dy ? (box.bottom - from.y) / dy : Infinity;
  return Math.max(0, Math.min(x1, x2), Math.min(y1, y2)) <
    Math.min(1, Math.max(x1, x2), Math.max(y1, y2));
}

function clearSegment(from, to, boxes) {
  return !boxes.some(box => segmentHitsBox(from, to, box));
}

function expand(box, padding) {
  return {
    left: box.left - padding, right: box.right + padding,
    top: box.top - padding, bottom: box.bottom + padding,
  };
}

function distance(a, b) {
  return Math.hypot(b.x - a.x, b.y - a.y);
}

function directCurve(start, end, boxes) {
  const gap = end.x - start.x;
  if (gap <= 0) return null;
  const handle = Math.min(90, gap * .45);
  const a = { x: start.x + handle, y: start.y };
  const b = { x: end.x - handle, y: end.y };
  const points = [start, a, b, end];
  let previous = start;
  for (let i = 1; i <= 12; i++) {
    const point = pointOnCubic(points, i / 12);
    if (!clearSegment(previous, point, boxes)) return null;
    previous = point;
  }
  return points;
}

function createDetourRouter(cards, padding) {
  const expanded = cards.map(card => expand(card, padding));
  const corners = expanded.flatMap(box => [
    { x: box.left, y: box.top }, { x: box.right, y: box.top },
    { x: box.left, y: box.bottom }, { x: box.right, y: box.bottom },
  ]);
  // Visibility between obstacle corners is shared by all connections in this draw.
  const cornerLinks = new Map();
  function visible(points, i, j) {
    if (i < 2 || j < 2) return clearSegment(points[i], points[j], expanded);
    const key = Math.min(i, j) * points.length + Math.max(i, j);
    if (!cornerLinks.has(key)) cornerLinks.set(key, clearSegment(points[i], points[j], expanded));
    return cornerLinks.get(key);
  }

  return (start, end, sourceIndex, targetIndex) => {
    const exit = { x: start.x + padding, y: start.y };
    const entry = { x: end.x - padding, y: end.y };
    const otherCards = expanded.filter((_, index) => index !== sourceIndex && index !== targetIndex);
    if (!clearSegment(start, exit, otherCards) || !clearSegment(entry, end, otherCards)) return null;
    const points = [exit, entry, ...corners];
    const costs = points.map(() => Infinity);
    const remaining = points.map(point => distance(point, entry));
    const parents = points.map(() => -1);
    const visited = new Set();
    costs[0] = 0;
    while (!visited.has(1)) {
      let nearest = -1;
      let estimate = Infinity;
      for (let i = 0; i < points.length; i++) {
        const score = costs[i] + remaining[i];
        if (!visited.has(i) && score < estimate) {
          nearest = i;
          estimate = score;
        }
      }
      if (nearest < 0) return null;
      visited.add(nearest);
      if (nearest === 1) break;
      for (let i = 1; i < points.length; i++) {
        if (visited.has(i)) continue;
        const cost = costs[nearest] + distance(points[nearest], points[i]) + turnCost;
        if (cost < costs[i] && visible(points, nearest, i)) {
          costs[i] = cost;
          parents[i] = nearest;
        }
      }
    }
    const path = [end];
    for (let i = 1; i >= 0; i = parents[i]) path.push(points[i]);
    path.push(start);
    return { points: path.reverse(), radius: padding - 1 };
  };
}

export function createEdgeRouter(cards) {
  const expanded = cards.map(card => expand(card, clearance));
  let detour;
  let tightDetour;
  return (sourceIndex, targetIndex) => {
    const source = cards[sourceIndex];
    const target = cards[targetIndex];
    const start = { x: source.right + 3, y: (source.top + source.bottom) / 2 };
    const end = { x: target.left - 4, y: (target.top + target.bottom) / 2 };
    const otherCards = expanded.filter((_, index) => index !== sourceIndex && index !== targetIndex);
    const cubic = directCurve(start, end, otherCards);
    if (cubic) return { points: [start, end], cubic };

    detour ??= createDetourRouter(cards, clearance);
    const path = detour(start, end, sourceIndex, targetIndex);
    if (path) return path;
    tightDetour ??= createDetourRouter(cards, 3);
    const tightPath = tightDetour(start, end, sourceIndex, targetIndex);
    if (tightPath) return tightPath;

    // A card can physically cover a port while the user drags it.
    return { points: [start, end], cubic: [
      start, { x: start.x + clearance, y: start.y },
      { x: end.x - clearance, y: end.y }, end,
    ] };
  };
}

function toward(from, to, amount) {
  const fraction = amount / distance(from, to);
  return { x: from.x + (to.x - from.x) * fraction, y: from.y + (to.y - from.y) * fraction };
}

export function traceEdge(ctx, { points, cubic, radius }) {
  ctx.moveTo(points[0].x, points[0].y);
  if (cubic) {
    ctx.bezierCurveTo(cubic[1].x, cubic[1].y, cubic[2].x, cubic[2].y, cubic[3].x, cubic[3].y);
    return;
  }

  for (let i = 1; i < points.length - 1; i++) {
    const previous = points[i - 1];
    const corner = points[i];
    const next = points[i + 1];
    const bend = Math.min(radius, distance(corner, previous) * .45, distance(corner, next) * .45);
    if (!bend) continue;
    const before = toward(corner, previous, bend);
    const after = toward(corner, next, bend);
    ctx.lineTo(before.x, before.y);
    ctx.quadraticCurveTo(corner.x, corner.y, after.x, after.y);
  }
  const end = points.at(-1);
  ctx.lineTo(end.x, end.y);
}

function pointOnCubic([a, b, c, d], t) {
  const s = 1 - t;
  return {
    x: s ** 3 * a.x + 3 * s ** 2 * t * b.x + 3 * s * t ** 2 * c.x + t ** 3 * d.x,
    y: s ** 3 * a.y + 3 * s ** 2 * t * b.y + 3 * s * t ** 2 * c.y + t ** 3 * d.y,
  };
}

export function pointAlongEdge({ points, cubic }, fraction) {
  if (cubic) return pointOnCubic(cubic, fraction);
  const lengths = points.slice(1).map((point, index) => distance(points[index], point));
  let remaining = lengths.reduce((total, length) => total + length, 0) * fraction;
  for (const [index, length] of lengths.entries()) {
    if (length && remaining <= length) return toward(points[index], points[index + 1], remaining);
    remaining -= length;
  }
  return points.at(-1);
}
