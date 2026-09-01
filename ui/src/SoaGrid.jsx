import React, { useMemo } from 'react'

/**
 * Renders one schedule as a grid that mirrors the printed table.
 *
 * Four things here exist specifically so the output can be checked against the source
 * rather than merely displayed:
 *
 *  - hierarchical headers are drawn as real spanning cells, so a study-period banner sits
 *    above the visit columns it actually covers;
 *  - category rows are drawn as full-width banners rather than as assessments, because
 *    conflating the two is exactly the structural error the extraction is meant to avoid;
 *  - the printed grid is drawn as printed: a column the source rules but leaves empty
 *    keeps its place, and several activities inside one row-label cell stay in that one
 *    row. Both are marked, so a reader can see the difference between "the page is blank
 *    here" and "we dropped something";
 *  - every data cell is clickable and reports its bounding box upward, so the page image
 *    beside it can highlight the region the value came from, and picking a footnote lights
 *    up every cell, row and column its marker sits on.
 */

function buildGroupRows(schedule) {
  // Column groups may nest. Each level becomes one header row of spanning cells.
  const byLevel = new Map()
  for (const g of schedule.column_groups || []) {
    if (!byLevel.has(g.level)) byLevel.set(g.level, [])
    byLevel.get(g.level).push(g)
  }
  return [...byLevel.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([level, groups]) => ({ level, groups: groups.slice().sort(sortBySpanStart(schedule)) }))
}

function sortBySpanStart(schedule) {
  const order = new Map(schedule.columns.map((c, i) => [c.id, i]))
  return (a, b) => (order.get(a.span?.[0]) ?? 0) - (order.get(b.span?.[0]) ?? 0)
}

/** Header rows are only as deep as the deepest column, so build the union of roles. */
function headerRowCount(schedule) {
  return Math.max(0, ...schedule.columns.map((c) => c.header_cells?.length || 0))
}

/**
 * Everything a footnote marker sits on: cells, rows, columns and groups.
 *
 * Read from two places on purpose. `cell.footnote_refs` is what the engine saw printed
 * inside the cell; `footnote.attached_to` is what the linker worked out afterwards,
 * including anchors on a whole row or a whole column that no single cell records. A
 * reviewer checking a footnote wants both.
 */
function footnoteTargets(schedule, marker) {
  const empty = { cells: new Set(), rows: new Set(), columns: new Set(), groups: new Set() }
  if (!marker) return empty

  const hit = (refs) => (refs || []).some((m) => m === marker)
  const out = {
    cells: new Set((schedule.cells || []).filter((c) => hit(c.footnote_refs)).map((c) => `${c.row_id}|${c.column_id}`)),
    rows: new Set((schedule.rows || []).filter((r) => hit(r.footnote_refs)).map((r) => r.id)),
    columns: new Set((schedule.columns || []).filter((c) => hit(c.footnote_refs)).map((c) => c.id)),
    groups: new Set(
      [...(schedule.row_groups || []), ...(schedule.column_groups || [])]
        .filter((g) => hit(g.footnote_refs))
        .map((g) => g.id)
    ),
  }

  for (const fn of schedule.footnotes || []) {
    if (fn.marker !== marker) continue
    for (const a of fn.attached_to || []) {
      if (a.kind === 'cell' && a.row_id && a.column_id) out.cells.add(`${a.row_id}|${a.column_id}`)
      else if (a.kind === 'row' && a.row_id) out.rows.add(a.row_id)
      else if (a.kind === 'column' && a.column_id) out.columns.add(a.column_id)
      else if (a.group_id) out.groups.add(a.group_id)
    }
  }
  return out
}

function Markers({ refs, onPick, active }) {
  if (!refs || refs.length === 0) return null
  return (
    <>
      {refs.map((m, i) => (
        <span
          key={`${m}-${i}`}
          className={`marker${m === active ? ' active' : ''}`}
          title={`Footnote ${m} — click to show its text and light up everything it marks`}
          onClick={(e) => {
            e.stopPropagation()
            onPick?.(m)
          }}
        >
          {m}
        </span>
      ))}
    </>
  )
}

/**
 * A row label as the source prints it.
 *
 * One line per activity named in the row's single label cell. More than one line means
 * the page drew one row against several activities; the badge says so, because the
 * alternative reading -- three rows, two of them empty -- is the mistake this is here to
 * make visible.
 */
function RowLabel({ row }) {
  const lines = row.label_lines?.length ? row.label_lines : [row.label]
  if (!row.merged_label || lines.length < 2) return <>{lines.join(' ')}</>
  return (
    <>
      {lines.map((line, i) => (
        <div className="label-line" key={i}>
          {line}
        </div>
      ))}
      <span
        className="pill merged"
        title="The source draws these as ONE row, with one set of marks covering all of them. Kept as printed."
      >
        one printed row
      </span>
    </>
  )
}

export default function SoaGrid({
  schedule,
  selectedCell,
  onSelectCell,
  onPickFootnote,
  footnoteTarget,
}) {
  const columns = schedule.columns || []
  const cellIndex = new Map()
  for (const c of schedule.cells || []) cellIndex.set(`${c.row_id}|${c.column_id}`, c)

  const groupRows = buildGroupRows(schedule)
  const depth = headerRowCount(schedule)
  const groupById = new Map((schedule.row_groups || []).map((g) => [g.id, g]))
  const lit = useMemo(() => footnoteTargets(schedule, footnoteTarget), [schedule, footnoteTarget])

  // Rows the reconciliation added from the geometric engine alone are flagged for review.
  const flagged = new Set(
    (schedule.reconciliation?.warnings || [])
      .filter((w) => w.type === 'row_missing_in_engine' && w.row_id)
      .map((w) => w.row_id)
  )
  const geometricOnly = new Set(
    (schedule.rows || []).filter((r) => (r.engines || []).join() === 'geometric').map((r) => r.id)
  )

  // Interleave category banners back into the row flow at the point they introduce.
  const flow = []
  let lastGroup = null
  for (const row of schedule.rows || []) {
    const gid = row.group_path?.[row.group_path.length - 1]
    if (gid && gid !== lastGroup && groupById.has(gid)) {
      flow.push({ kind: 'category', group: groupById.get(gid) })
      lastGroup = gid
    }
    flow.push({ kind: 'row', row })
  }

  const totalCols = columns.length + 1

  return (
    <div className="grid-wrap">
      <table className="soa">
        <thead>
          {groupRows.map(({ level, groups }) => (
            <tr key={`g${level}`}>
              <th className="rowhead" style={{ top: 0 }} />
              {groups.map((g) => (
                <th
                  key={g.id}
                  colSpan={Math.max(1, g.span?.length || 1)}
                  title={g.label}
                  className={lit.groups.has(g.id) ? 'fn-lit' : ''}
                >
                  {g.label}
                  <Markers refs={g.footnote_refs} onPick={onPickFootnote} active={footnoteTarget} />
                </th>
              ))}
            </tr>
          ))}

          {Array.from({ length: depth }).map((_, level) => (
            <tr key={`h${level}`}>
              {level === 0 && (
                <th className="rowhead" rowSpan={depth}>
                  Assessment
                </th>
              )}
              {columns.map((col) => {
                const hc = col.header_cells?.[level]
                const classes = [
                  col.printed_blank ? 'blank-col' : '',
                  lit.columns.has(col.id) ? 'fn-lit' : '',
                ]
                  .filter(Boolean)
                  .join(' ')
                return (
                  <th
                    key={col.id}
                    className={classes}
                    title={
                      col.printed_blank
                        ? 'The source rules this column but prints no grid data in it — no visit, no week, no cells. Some tables rule one to hold a sideways divider such as RANDOMIZATION, which is kept as an annotation rather than as rows. It stays here so the visits to its right keep their printed positions.'
                        : undefined
                    }
                  >
                    {hc ? (
                      <>
                        {hc.text}
                        {level === 0 && hc.role && hc.role !== 'other' && (
                          <span className="role-tag">{hc.role.replace(/_/g, ' ')}</span>
                        )}
                      </>
                    ) : null}
                    {level === depth - 1 && col.printed_blank && (
                      <span className="role-tag">blank in source</span>
                    )}
                    {level === depth - 1 && (
                      <Markers refs={col.footnote_refs} onPick={onPickFootnote} active={footnoteTarget} />
                    )}
                  </th>
                )
              })}
            </tr>
          ))}

          {depth === 0 && (
            <tr>
              <th className="rowhead">Assessment</th>
              {columns.map((c) => (
                <th key={c.id} className={c.printed_blank ? 'blank-col' : ''}>
                  {c.index + 1}
                </th>
              ))}
            </tr>
          )}
        </thead>

        <tbody>
          {flow.map((item, i) =>
            item.kind === 'category' ? (
              <tr className="category" key={`c${item.group.id}-${i}`}>
                <td colSpan={totalCols} className={lit.groups.has(item.group.id) ? 'fn-lit' : ''}>
                  {item.group.label}
                  <Markers refs={item.group.footnote_refs} onPick={onPickFootnote} active={footnoteTarget} />
                </td>
              </tr>
            ) : (
              <tr
                key={item.row.id}
                className={flagged.has(item.row.id) || geometricOnly.has(item.row.id) ? 'flagged' : ''}
              >
                <td
                  className={`rowhead${item.row.merged_label ? ' merged-row' : ''}${
                    lit.rows.has(item.row.id) ? ' fn-lit' : ''
                  }`}
                  title={(item.row.label_lines || [item.row.label]).join('\n')}
                >
                  <RowLabel row={item.row} />
                  <Markers refs={item.row.footnote_refs} onPick={onPickFootnote} active={footnoteTarget} />
                  {geometricOnly.has(item.row.id) && (
                    <span className="pill" title="Read by the geometric engine only — check against the source">
                      geo only
                    </span>
                  )}
                </td>
                {columns.map((col) => {
                  const key = `${item.row.id}|${col.id}`
                  const cell = cellIndex.get(key)
                  const isSelected = selectedCell === key
                  const isLit = lit.cells.has(key)
                  if (!cell) {
                    return <td key={col.id} className={col.printed_blank ? 'blank-col' : ''} />
                  }
                  return (
                    <td
                      key={col.id}
                      className={`cell${isSelected ? ' selected' : ''}${isLit ? ' fn-lit' : ''}`}
                      colSpan={cell.col_span > 1 ? cell.col_span : undefined}
                      title={
                        cell.ambiguous
                          ? `Ambiguous: ${cell.notes || 'see source'}`
                          : cell.engines
                            ? Object.entries(cell.engines)
                                .map(([k, v]) => `${k}: ${v}`)
                                .join('\n')
                            : cell.raw
                      }
                      onClick={() => onSelectCell(isSelected ? null : key, cell)}
                    >
                      {cell.raw}
                      {cell.ambiguous && <span className="marker" title={cell.notes || ''}>?</span>}
                    </td>
                  )
                })}
              </tr>
            )
          )}
        </tbody>
      </table>
    </div>
  )
}
