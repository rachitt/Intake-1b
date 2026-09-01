import React from 'react'

/**
 * Renders one schedule as a grid that mirrors the printed table.
 *
 * Three things here exist specifically so the output can be checked against the source
 * rather than merely displayed:
 *
 *  - hierarchical headers are drawn as real spanning cells, so a study-period banner sits
 *    above the visit columns it actually covers;
 *  - category rows are drawn as full-width banners rather than as assessments, because
 *    conflating the two is exactly the structural error the extraction is meant to avoid;
 *  - every data cell is clickable and reports its bounding box upward, so the page image
 *    beside it can highlight the region the value came from.
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

function Markers({ refs, onPick }) {
  if (!refs || refs.length === 0) return null
  return (
    <>
      {refs.map((m, i) => (
        <span
          key={`${m}-${i}`}
          className="marker"
          title={`Footnote ${m} — click to show its text`}
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

export default function SoaGrid({ schedule, selectedCell, onSelectCell, onPickFootnote }) {
  const columns = schedule.columns || []
  const cellIndex = new Map()
  for (const c of schedule.cells || []) cellIndex.set(`${c.row_id}|${c.column_id}`, c)

  const groupRows = buildGroupRows(schedule)
  const depth = headerRowCount(schedule)
  const groupById = new Map((schedule.row_groups || []).map((g) => [g.id, g]))

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
                <th key={g.id} colSpan={Math.max(1, g.span?.length || 1)} title={g.label}>
                  {g.label}
                  <Markers refs={g.footnote_refs} onPick={onPickFootnote} />
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
                return (
                  <th key={col.id}>
                    {hc ? (
                      <>
                        {hc.text}
                        {level === 0 && hc.role && hc.role !== 'other' && (
                          <span className="role-tag">{hc.role.replace(/_/g, ' ')}</span>
                        )}
                      </>
                    ) : null}
                    {level === depth - 1 && <Markers refs={col.footnote_refs} onPick={onPickFootnote} />}
                  </th>
                )
              })}
            </tr>
          ))}

          {depth === 0 && (
            <tr>
              <th className="rowhead">Assessment</th>
              {columns.map((c) => (
                <th key={c.id}>{c.index + 1}</th>
              ))}
            </tr>
          )}
        </thead>

        <tbody>
          {flow.map((item, i) =>
            item.kind === 'category' ? (
              <tr className="category" key={`c${item.group.id}-${i}`}>
                <td colSpan={totalCols}>
                  {item.group.label}
                  <Markers refs={item.group.footnote_refs} onPick={onPickFootnote} />
                </td>
              </tr>
            ) : (
              <tr
                key={item.row.id}
                className={flagged.has(item.row.id) || geometricOnly.has(item.row.id) ? 'flagged' : ''}
              >
                <td className="rowhead" title={item.row.label}>
                  {item.row.label}
                  <Markers refs={item.row.footnote_refs} onPick={onPickFootnote} />
                  {geometricOnly.has(item.row.id) && (
                    <span className="pill" title="Read by the geometric engine only — check against the source">
                      geo only
                    </span>
                  )}
                </td>
                {columns.map((col) => {
                  const cell = cellIndex.get(`${item.row.id}|${col.id}`)
                  const key = `${item.row.id}|${col.id}`
                  const isSelected = selectedCell === key
                  if (!cell) return <td key={col.id} />
                  return (
                    <td
                      key={col.id}
                      className={`cell${isSelected ? ' selected' : ''}`}
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
