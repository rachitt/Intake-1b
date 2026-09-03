import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import SoaGrid from './SoaGrid.jsx'

const POLL_MS = 1500

function Uploader({ onStart, busy }) {
  const [drag, setDrag] = useState(false)
  const [useVision, setUseVision] = useState(true)
  const inputRef = useRef(null)

  const handle = (file) => {
    if (file) onStart(file, useVision)
  }

  return (
    <div
      className={`uploader${drag ? ' drag' : ''}`}
      onDragOver={(e) => {
        e.preventDefault()
        setDrag(true)
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDrag(false)
        handle(e.dataTransfer.files?.[0])
      }}
    >
      <h2>Drop a clinical trial protocol PDF</h2>
      <p>
        The tool locates the Schedule of Activities itself — no page numbers are supplied —
        then extracts it and shows the result beside the source page so you can check it.
      </p>
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf"
        style={{ display: 'none' }}
        onChange={(e) => handle(e.target.files?.[0])}
      />
      <button className="primary" disabled={busy} onClick={() => inputRef.current?.click()}>
        {busy ? 'Working…' : 'Choose a PDF'}
      </button>
      <label>
        <input type="checkbox" checked={useVision} onChange={(e) => setUseVision(e.target.checked)} />
        Use the vision engine (uncheck for a free, text-layer-only run)
      </label>
    </div>
  )
}

function PageImages({ jobId, pages, highlight }) {
  const [sizes, setSizes] = useState({})

  useEffect(() => {
    let cancelled = false
    Promise.all(
      pages.map((p) =>
        fetch(`/api/jobs/${jobId}/page/${p}/size`)
          .then((r) => (r.ok ? r.json() : null))
          .then((d) => [p, d])
          .catch(() => [p, null])
      )
    ).then((entries) => {
      if (!cancelled) setSizes(Object.fromEntries(entries))
    })
    return () => {
      cancelled = true
    }
  }, [jobId, pages.join(',')])

  const targetRef = useRef(null)
  useEffect(() => {
    if (highlight && targetRef.current) {
      targetRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [highlight])

  return (
    <>
      {pages.map((p) => {
        const size = sizes[p]
        const isTarget = highlight && highlight.page === p
        // Boxes come back in PDF points; the rendered image is a scaled copy of the same
        // page, so a percentage of the page dimensions positions them at any zoom level.
        const box =
          isTarget && size
            ? {
                left: `${(highlight.x0 / size.width) * 100}%`,
                top: `${(highlight.y0 / size.height) * 100}%`,
                width: `${((highlight.x1 - highlight.x0) / size.width) * 100}%`,
                height: `${((highlight.y1 - highlight.y0) / size.height) * 100}%`,
              }
            : null
        return (
          <div className="page-image" key={p} ref={isTarget ? targetRef : null}>
            <div className="page-caption">
              page {p}
              {size ? ` · ${size.orientation} · ${Math.round(size.width)}×${Math.round(size.height)}pt` : ''}
            </div>
            <img src={`/api/jobs/${jobId}/page/${p}.png`} alt={`Page ${p}`} loading="lazy" />
            {box && <div className="bbox" style={box} />}
          </div>
        )
      })}
    </>
  )
}

function Warnings({ reconciliation }) {
  const warnings = reconciliation?.warnings || []
  if (warnings.length === 0) {
    return <p className="meta">No disagreements between the two engines.</p>
  }
  const order = { high: 0, medium: 1, low: 2 }
  const sorted = [...warnings].sort((a, b) => order[a.severity] - order[b.severity])
  return (
    <>
      {sorted.map((w, i) => (
        <div className={`warn ${w.severity}`} key={i}>
          <div className="type">{w.type.replace(/_/g, ' ')}</div>
          {w.message}
        </div>
      ))}
    </>
  )
}

function Footnotes({ schedule, target, onPick }) {
  const rowById = useMemo(
    () =>
      new Map(
        (schedule.rows || []).map((r) => [r.id, (r.label_lines?.length ? r.label_lines : [r.label]).join(' / ')])
      ),
    [schedule]
  )
  const colById = useMemo(
    () =>
      new Map(
        (schedule.columns || []).map((c) => [
          c.id,
          (c.header_cells || []).map((h) => h.text).filter(Boolean).join(' / ') || `column ${c.index + 1}`,
        ])
      ),
    [schedule]
  )

  if (!schedule.footnotes?.length) return <p className="empty">No footnotes found.</p>

  const describe = (a) => {
    if (a.kind === 'cell') return `${rowById.get(a.row_id) || a.row_id} × ${colById.get(a.column_id) || a.column_id}`
    if (a.kind === 'row') return `row: ${rowById.get(a.row_id) || a.row_id}`
    if (a.kind === 'column') return `column: ${colById.get(a.column_id) || a.column_id}`
    return `${a.kind}: ${a.group_id || a.detail || ''}`
  }

  return (
    <>
      {schedule.footnotes.map((f, i) => {
        const hit = target && (f.marker === target || f.marker.replace(/^X/i, '') === target)
        return (
          <div className={`footnote${hit ? ' target' : ''}`} key={`${f.marker}-${i}`} onClick={() => onPick(f.marker)}>
            <span className="fmarker">{f.marker}</span>
            {f.text}
            {f.pages?.length > 1 && (
              <div className="spill">
                ⤷ text continues across pages {f.pages.join(' → ')}
              </div>
            )}
            {!f.text_complete && <div className="unlinked">⚠ text may be truncated</div>}
            {f.attached_to?.length > 0 ? (
              <div className="anchors">
                attached to {f.attached_to.length} element{f.attached_to.length === 1 ? '' : 's'}:{' '}
                {f.attached_to.slice(0, 4).map(describe).join('; ')}
                {f.attached_to.length > 4 ? ` … +${f.attached_to.length - 4} more` : ''}
              </div>
            ) : (
              <div className="unlinked">⚠ not linked to any cell, row or header</div>
            )}
          </div>
        )
      })}
    </>
  )
}

export default function App() {
  const [job, setJob] = useState(null)
  const [state, setState] = useState(null)
  const [error, setError] = useState(null)
  const [tab, setTab] = useState(0)
  const [selectedCell, setSelectedCell] = useState(null)
  const [highlight, setHighlight] = useState(null)
  const [footnoteTarget, setFootnoteTarget] = useState(null)

  const start = useCallback(async (file, useVision) => {
    setError(null)
    setState(null)
    setSelectedCell(null)
    setHighlight(null)
    setTab(0)
    const body = new FormData()
    body.append('file', file)
    try {
      const res = await fetch(`/api/extract?use_vision=${useVision}`, { method: 'POST', body })
      if (!res.ok) throw new Error((await res.json()).detail || `HTTP ${res.status}`)
      const data = await res.json()
      setJob(data.job_id)
      setState({ status: 'queued', filename: data.filename })
    } catch (e) {
      setError(String(e.message || e))
    }
  }, [])

  useEffect(() => {
    if (!job || state?.status === 'done' || state?.status === 'error') return
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`/api/jobs/${job}`)
        const data = await res.json()
        setState(data)
        if (data.status === 'error') setError(data.error)
      } catch (e) {
        setError(String(e))
      }
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [job, state?.status])

  const result = state?.status === 'done' ? state.result : null
  const schedules = result?.schedules || []
  const schedule = schedules[tab]

  const pages = useMemo(() => {
    if (!schedule) return []
    return [...new Set([...(schedule.pages || []), ...(schedule.footnote_pages || [])])].sort(
      (a, b) => a - b
    )
  }, [schedule])

  const handleSelectCell = (key, cell) => {
    setSelectedCell(key)
    setFootnoteTarget(null)
    setHighlight(key && cell?.bbox ? cell.bbox : null)
  }

  // Picking a footnote lights up everything its marker sits on, in the extracted grid only.
  // Deliberately not on the page image: a marker routinely sits on dozens of cells across
  // both pages, and boxing an arbitrary one of them points somewhere misleading. The page
  // box stays reserved for the one thing it can answer exactly -- where a single cell the
  // reviewer clicked came from -- so any box on the image always means that and nothing else.
  const pickFootnote = useCallback(
    (marker) => {
      const next = marker === footnoteTarget ? null : marker
      setFootnoteTarget(next)
      setSelectedCell(null)
      setHighlight(null)
    },
    [footnoteTarget]
  )

  if (!job) {
    return (
      <div className="app">
        <div className="topbar">
          <h1>Schedule of Activities extraction</h1>
        </div>
        {error && <div className="error" style={{ margin: 16 }}>{error}</div>}
        <Uploader onStart={start} busy={false} />
      </div>
    )
  }

  return (
    <div className="app">
      <div className="topbar">
        <h1>Schedule of Activities extraction</h1>
        <span className="filename">{state?.filename}</span>
        <span className="stage">
          {state?.status}
          {state?.stage ? ` · ${state.stage}` : ''}
          {state?.detail ? ` · ${state.detail}` : ''}
        </span>
        <div className="spacer" />
        {result && (
          <a href={`/api/jobs/${job}/download`}>
            <button>Download JSON</button>
          </a>
        )}
        <button
          onClick={() => {
            setJob(null)
            setState(null)
            setError(null)
          }}
        >
          New file
        </button>
      </div>

      {error && <div className="error" style={{ margin: 16 }}>{error}</div>}

      {!result && !error && (
        <div className="empty" style={{ padding: 24 }}>
          Locating the schedule and extracting it. A protocol with several pages of tables
          usually takes one to three minutes.
        </div>
      )}

      {result && schedules.length === 0 && (
        <div className="empty" style={{ padding: 24 }}>
          No Schedule of Activities was found in this document. The locator reports what it
          considered in the JSON download.
        </div>
      )}

      {schedule && (
        <>
          {schedules.length > 1 && (
            <div className="tabs">
              {schedules.map((s, i) => (
                <button
                  key={s.id}
                  className={`tab${i === tab ? ' active' : ''}`}
                  onClick={() => {
                    setTab(i)
                    setSelectedCell(null)
                    setHighlight(null)
                  }}
                >
                  {s.heading.slice(0, 46)}
                  <span className="pill">{s.kind}</span>
                </button>
              ))}
            </div>
          )}

          <div className="split">
            <div className="pane-left">
              <div className="section">
                <h3>Source pages</h3>
                <PageImages jobId={job} pages={pages} highlight={highlight} />
              </div>
            </div>

            <div className="pane-right">
              <div className="section">
                <h3>
                  {schedule.heading}
                  <span className="pill">{schedule.kind}</span>
                </h3>
                <p className="meta">
                  pages {schedule.pages.join(', ')} · {schedule.columns.length} columns ·{' '}
                  {schedule.rows.length} rows · {schedule.cells.length} cells ·{' '}
                  {schedule.footnotes.length} footnotes
                  {schedule.rotated_annotations?.length > 0 &&
                    ` · rotated annotations kept out of the grid: ${schedule.rotated_annotations
                      .map((r) => r.text)
                      .join(', ')}`}
                </p>
                <p className="meta">
                  Click any cell to highlight where it came from on the page. Click a
                  footnote marker, or a footnote below, to light up every cell, row and
                  column it is attached to in the table.
                </p>
                <SoaGrid
                  schedule={schedule}
                  selectedCell={selectedCell}
                  onSelectCell={handleSelectCell}
                  onPickFootnote={pickFootnote}
                  footnoteTarget={footnoteTarget}
                />
              </div>

              <div className="section">
                <h3>Recall check</h3>
                <p className="meta">
                  engines: {(schedule.reconciliation?.engines_run || []).join(' + ') || 'none'} ·
                  rows {JSON.stringify(schedule.reconciliation?.row_counts || {})} · columns{' '}
                  {JSON.stringify(schedule.reconciliation?.column_counts || {})}
                  {schedule.reconciliation?.cell_agreement != null &&
                    ` · cell agreement ${(schedule.reconciliation.cell_agreement * 100).toFixed(0)}%`}
                </p>
                <Warnings reconciliation={schedule.reconciliation} />
              </div>

              <div className="section">
                <h3>Footnotes</h3>
                <Footnotes schedule={schedule} target={footnoteTarget} onPick={pickFootnote} />
              </div>

              {(schedule.assumptions?.length > 0 || schedule.open_questions?.length > 0) && (
                <div className="section">
                  <h3>Assumptions and open questions</h3>
                  {schedule.assumptions?.map((a, i) => (
                    <div className="warn low" key={`a${i}`}>{a}</div>
                  ))}
                  {schedule.open_questions?.map((q, i) => (
                    <div className="warn medium" key={`q${i}`}>
                      <div className="type">for a clinical SME</div>
                      {q}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
