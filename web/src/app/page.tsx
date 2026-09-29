'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowDown,
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  Copy,
  Ellipsis,
  Globe,
  PackageOpen,
  Pencil,
  Play,
  Plus,
  Search,
  Square,
  Trash2,
  Users,
} from 'lucide-react'

import { EmptyState } from '@/components/empty-state'
import { ConfirmDialog } from '@/components/modal'
import { ProfileForm } from '@/components/profile-form'
import { useToast } from '@/components/toast'
import {
  browsersAPI,
  formatLastUsed,
  formatProxyString,
  hasGeography,
  OS_LABELS,
  profilesAPI,
  readProxyCheck,
  type Profile,
  type ProxyCheckRecord,
} from '@/lib/api'
import { useLang, useT } from '@/lib/i18n'

type SortKey = 'name' | 'id' | 'os' | 'status' | 'last_used'

const COLUMN_KEYS: { key: SortKey; labelKey: string; className?: string }[] = [
  { key: 'name', labelKey: 'pl.colName' },
  { key: 'id', labelKey: 'pl.colId' },
  { key: 'os', labelKey: 'pl.colOs' },
  { key: 'status', labelKey: 'pl.colStatus' },
  { key: 'last_used', labelKey: 'pl.colLastUsed' },
]

export default function ProfilesPage() {
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [running, setRunning] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [sortKey, setSortKey] = useState<SortKey>('name')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const [page, setPage] = useState(1)
  const perPage = 25

  const [checkingProxies, setCheckingProxies] = useState<Set<string>>(new Set())
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [menuFor, setMenuFor] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Profile | null>(null)
  const [confirm, setConfirm] = useState<null | {
    title: string
    body: string
    label: string
    run: () => Promise<void>
  }>(null)
  const toast = useToast()
  const archiveRef = useRef<HTMLInputElement>(null)
  const t = useT()
  const { lang } = useLang()

  const loadProfiles = useCallback(async () => {
    try {
      setError(null)
      // The API paginates; pull every page so sorting and search stay client-side
      // and instant. Guarded so a bad has_next can never spin forever.
      const collected: Profile[] = []
      for (let pageNumber = 1; pageNumber <= 200; pageNumber += 1) {
        const response = await profilesAPI.getProfiles({ page: pageNumber, per_page: 100 })
        collected.push(...response.profiles)
        if (!response.has_next) break
      }
      setProfiles(collected)
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err))
    } finally {
      setLoading(false)
    }
  }, [])

  const loadRunning = useCallback(async () => {
    try {
      const response = await browsersAPI.active()
      setRunning(new Set(response.active_browsers.map((browser) => browser.profile_id)))
    } catch {
      // The poll is best-effort; a transient failure must not surface as an error.
    }
  }, [])

  useEffect(() => {
    // loadProfiles clears the previous error synchronously before awaiting,
    // which is the one render the rule objects to and is what makes a reload
    // stop showing a stale failure.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadProfiles()
    loadRunning()
    const timer = setInterval(loadRunning, 5000)
    return () => clearInterval(timer)
  }, [loadProfiles, loadRunning])

  useEffect(() => {
    // Close on any click that is not on a trigger or inside an open menu.
    // Relying on stopPropagation in the trigger's React handler is not enough:
    // React delegates to its root container, so the document listener could
    // still fire and shut the menu in the same click that opened it.
    const close = (event: MouseEvent) => {
      const target = event.target as Element | null
      if (target?.closest('[data-menu-trigger], [role="menu"]')) return
      setMenuFor(null)
    }
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [])

  // Go back to the first page when the filters change. Adjusted during render
  // rather than in an effect: an effect would paint the new results clamped to
  // whatever page number survived, then snap to the first one, and React
  // documents this as the way to derive state from a change in props or state.
  const [filterForPage, setFilterForPage] = useState(() => ({ search, statusFilter }))
  if (filterForPage.search !== search || filterForPage.statusFilter !== statusFilter) {
    setFilterForPage({ search, statusFilter })
    setPage(1)
  }

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase()
    const filtered = profiles.filter((profile) => {
      if (statusFilter !== 'all' && profile.status !== statusFilter) return false
      if (!needle) return true
      return (
        profile.name.toLowerCase().includes(needle) || profile.id.toLowerCase().includes(needle)
      )
    })

    const direction = sortDir === 'asc' ? 1 : -1
    return [...filtered].sort((a, b) => {
      switch (sortKey) {
        case 'id':
          return a.id.localeCompare(b.id) * direction
        case 'os':
          return (a.browser_settings?.os ?? '').localeCompare(b.browser_settings?.os ?? '') * direction
        case 'status':
          return a.status.localeCompare(b.status) * direction
        case 'last_used':
          return (
            (new Date(a.last_used ?? 0).getTime() - new Date(b.last_used ?? 0).getTime()) * direction
          )
        default:
          return a.name.localeCompare(b.name) * direction
      }
    })
  }, [profiles, search, statusFilter, sortKey, sortDir])

  // Offering the bulk clear when nothing selected would change is just noise.
  const selectedWithGeography = useMemo(
    () => profiles.filter((profile) => selected.has(profile.id) && hasGeography(profile)).length,
    [profiles, selected],
  )

  // Same rule as the clear above: a profile with no proxy has nothing to check.
  const selectedWithProxy = useMemo(
    () => profiles.filter((profile) => selected.has(profile.id) && profile.proxy_config).length,
    [profiles, selected],
  )

  const totalPages = Math.max(1, Math.ceil(visible.length / perPage))
  // Deleting the last rows of a page must not strand the user on an empty one.
  const currentPage = Math.min(page, totalPages)
  const pageRows = visible.slice((currentPage - 1) * perPage, currentPage * perPage)
  const allOnPageSelected = pageRows.length > 0 && pageRows.every((row) => selected.has(row.id))

  /** Close the row menu and hand focus back to the button that opened it.
   *
   * Focus moves first: removing the menu while one of its items still has focus
   * drops focus to <body>, and a refocus queued after the close races that reset.
   */
  function closeMenu(profileId: string) {
    document.querySelector<HTMLButtonElement>(`[data-menu-trigger="${profileId}"]`)?.focus()
    setMenuFor(null)
  }

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  async function withBusy(id: string, action: () => Promise<void>) {
    setBusy((current) => new Set(current).add(id))
    try {
      await action()
    } finally {
      setBusy((current) => {
        const next = new Set(current)
        next.delete(id)
        return next
      })
    }
  }

  async function launch(profile: Profile) {
    await withBusy(profile.id, async () => {
      try {
        await profilesAPI.startProfile(profile.id)
        await Promise.all([loadRunning(), loadProfiles()])
      } catch (err) {
        toast('error', t('pl.launchErr'), String(err))
      }
    })
  }

  async function stop(profile: Profile) {
    await withBusy(profile.id, async () => {
      try {
        await profilesAPI.closeProfile(profile.id)
        await loadRunning()
      } catch (err) {
        toast('error', t('pl.closeErr'), String(err))
      }
    })
  }

  function download(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  }

  async function exportArchive(profile: Profile) {
    toast('info', t('pl.packingTitle'), t('pl.packingBody'))
    try {
      const blob = await profilesAPI.exportArchive(profile.id)
      const safe = profile.name.replace(/[^A-Za-z0-9_.-]+/g, '-').replace(/^-|-$/g, '')
      download(blob, `${safe || profile.id}.camoufox.zip`)
      toast('ok', t('pl.exportedTitle'), t('pl.exportedBody'))
    } catch (err) {
      toast('error', t('pl.exportErr'), String(err))
    }
  }

  async function importArchive(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    try {
      const profile = await profilesAPI.importArchive(file)
      toast('ok', t('pl.importedTitle'), profile.name)
      loadProfiles()
    } catch (err) {
      toast('error', t('pl.importErr'), String(err))
    }
  }

  async function clone(profile: Profile) {
    try {
      await profilesAPI.cloneProfile(profile.id, `${profile.name} copy`)
      toast('ok', t('pl.clonedTitle'), `${profile.name} copy`)
      loadProfiles()
    } catch (err) {
      toast('error', t('pl.cloneErr'), String(err))
    }
  }

  /**
   * Check one proxy and leave the answer in its row.
   *
   * The row is the report, not a toast: a toast is gone in four seconds, and the
   * question this answers — is this proxy alive, and does it agree with the
   * profile — is one the list should keep showing. Only a failure to reach *our
   * own API* is worth interrupting for, because then no row can say anything.
   */
  async function checkProxy(profile: Profile): Promise<ProxyCheckRecord | null> {
    setCheckingProxies((current) => new Set(current).add(profile.id))
    try {
      const result = await profilesAPI.checkProxy(profile.id)
      const record: ProxyCheckRecord = {
        checked_at: result.checked_at ?? new Date().toISOString(),
        reachable: result.reachable,
        error: result.error,
        latency_ms: result.latency_ms,
        ip: result.location?.ip ?? null,
        country: result.location?.country ?? null,
        timezone: result.location?.timezone ?? null,
        findings: result.findings,
      }
      setProfiles((current) =>
        current.map((row) => (row.id === profile.id ? { ...row, proxy_check: record } : row)),
      )
      return record
    } catch (err) {
      toast('error', t('pl.checkErr'), String(err))
      return null
    } finally {
      setCheckingProxies((current) => {
        const next = new Set(current)
        next.delete(profile.id)
        return next
      })
    }
  }

  /**
   * Check every selected profile that has a proxy.
   *
   * A few at a time: a hundred proxies opened at once is a hundred sockets and a
   * self-inflicted timeout, and rows landing one by one reads as progress where
   * a single wait reads as a hang.
   */
  async function checkSelectedProxies() {
    const queue = profiles.filter((profile) => selected.has(profile.id) && profile.proxy_config)
    if (!queue.length) return

    // Counted before the workers start draining the queue.
    const total = queue.length
    // Three outcomes, not two: a check that never reached our own API left its
    // row untouched, and reporting that as success is how a summary ends up
    // contradicting the table it summarises.
    let clean = 0
    let flagged = 0
    let unchecked = 0
    const workers = Array.from({ length: Math.min(5, total) }, async () => {
      for (let next = queue.shift(); next; next = queue.shift()) {
        const record = await checkProxy(next)
        if (!record) unchecked += 1
        else if (readProxyCheck(record, lang).tone === 'ok') clean += 1
        else flagged += 1
      }
    })
    await Promise.all(workers)

    // Worded from the same rule the dots use, so a red row is never counted as
    // an answer just because the proxy replied.
    const parts = [
      flagged ? t('pl.summaryNeedAttention', { n: flagged }) : null,
      unchecked ? t('pl.summaryUnchecked', { n: unchecked }) : null,
    ].filter(Boolean)

    if (parts.length)
      toast('error', t('pl.summaryTitle', { parts: parts.join(', ') }), t('pl.summaryErr', { total }))
    else toast('ok', clean === 1 ? t('pl.summaryOkSingle') : t('pl.summaryOk', { n: clean }))
  }

  /** English plural marker; Chinese needs none. */
  function plural(n: number): string {
    return lang === 'en' && n !== 1 ? 's' : ''
  }

  function askDelete(profile: Profile) {
    setConfirm({
      title: t('pl.delTitle'),
      body: t('pl.delBody', { name: profile.name }),
      label: t('pl.delLabel'),
      run: async () => {
        await profilesAPI.deleteProfile(profile.id)
        toast('ok', t('pl.deleted'), profile.name)
        setSelected((current) => {
          const next = new Set(current)
          next.delete(profile.id)
          return next
        })
        loadProfiles()
      },
    })
  }

  function askBulkDelete() {
    const count = selected.size
    const s = plural(count)
    setConfirm({
      title: t('pl.bulkDelTitle', { n: count, s }),
      body: t('pl.bulkDelBody'),
      label: t('pl.delLabel'),
      run: async () => {
        for (const id of selected) await profilesAPI.deleteProfile(id)
        toast('ok', t('pl.bulkDeleted', { n: count, s }))
        setSelected(new Set())
        loadProfiles()
      },
    })
  }

  /**
   * The profiles this exists for were all created the same way — with the
   * timezone and coordinates of a randomly chosen region — so the useful unit is
   * a selection, not one profile at a time. Only those that actually state a
   * location are named, so the confirmation says what will really change.
   */
  function askClearGeography() {
    const ids = profiles.filter((p) => selected.has(p.id) && hasGeography(p)).map((p) => p.id)
    const s = plural(ids.length)
    setConfirm({
      title: t('pl.clearGeoTitle', { n: ids.length, s }),
      body: t('pl.clearGeoBody'),
      label: t('pl.clearGeoLabel'),
      run: async () => {
        const result = await profilesAPI.clearGeography(ids)
        toast('ok', t('pl.cleared', { n: result.cleared.length, s: plural(result.cleared.length) }))
        setSelected(new Set())
        loadProfiles()
      },
    })
  }

  function askCloseAll() {
    const s = plural(running.size)
    setConfirm({
      title: t('pl.closeAllTitle'),
      body: t('pl.closeAllBody', { n: running.size, s }),
      label: t('pl.closeAllLabel'),
      run: async () => {
        const result = await browsersAPI.closeAll()
        toast('ok', result.message)
        loadRunning()
      },
    })
  }

  async function runConfirmed() {
    if (!confirm) return
    const action = confirm
    setConfirm(null)
    try {
      await action.run()
    } catch (err) {
      toast('error', t('pl.actionFailed'), String(err))
    }
  }

  return (
    <>
      <header className="sticky top-0 z-20 flex h-[52px] items-center gap-3 border-b border-line bg-canvas/85 px-5 backdrop-blur">
        <h1 className="text-[14px] font-semibold">{t('pl.title')}</h1>
        <span className="font-mono text-ink-faint">{visible.length}</span>

        <div className="relative ml-3 w-[240px]">
          <Search
            size={13}
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-faint"
          />
          <input
            className="field h-[30px] pl-7"
            placeholder={t('pl.searchPh')}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label={t('pl.searchAria')}
          />
        </div>

        <select
          className="field h-[30px] w-[132px]"
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value)}
          aria-label={t('pl.filterAria')}
        >
          <option value="all">{t('pl.optAll')}</option>
          <option value="active">{t('pl.optActive')}</option>
          <option value="inactive">{t('pl.optInactive')}</option>
          <option value="blocked">{t('pl.optBlocked')}</option>
          <option value="maintenance">{t('pl.optMaintenance')}</option>
        </select>

        <div className="ml-auto flex items-center gap-2">
          {running.size > 0 && (
            <button className="btn btn-default" onClick={askCloseAll}>
              <Square size={12} fill="currentColor" />
              {t('pl.closeRunning', { n: running.size })}
            </button>
          )}
          <button
            className="btn btn-ghost"
            onClick={() => archiveRef.current?.click()}
            title={t('pl.importTitle')}
          >
            <PackageOpen size={14} />
          </button>
          <input
            ref={archiveRef}
            type="file"
            accept=".zip"
            onChange={importArchive}
            className="hidden"
          />
          <button
            className="btn btn-primary"
            onClick={() => {
              setEditing(null)
              setFormOpen(true)
            }}
          >
            <Plus size={14} strokeWidth={2.5} />
            {t('pl.new')}
          </button>
        </div>
      </header>

      {selected.size > 0 && (
        <div className="flex items-center gap-3 border-b border-line bg-raised px-5 py-2">
          <span>{t('pl.selected', { n: selected.size })}</span>
          {selectedWithProxy > 0 && (
            <button
              className="btn btn-default h-7"
              disabled={checkingProxies.size > 0}
              onClick={checkSelectedProxies}
            >
              <Globe size={13} />
              {checkingProxies.size > 0
                ? t('pl.checking', { n: checkingProxies.size })
                : t('pl.checkProxies', { n: selectedWithProxy })}
            </button>
          )}
          {selectedWithGeography > 0 && (
            <button className="btn btn-default h-7" onClick={askClearGeography}>
              <Globe size={13} />
              {t('pl.clearGeo', { n: selectedWithGeography })}
            </button>
          )}
          <button className="btn btn-danger h-7" onClick={askBulkDelete}>
            <Trash2 size={13} />
            {t('pl.delete')}
          </button>
          <button className="btn btn-ghost h-7" onClick={() => setSelected(new Set())}>
            {t('pl.clearSel')}
          </button>
        </div>
      )}

      {error ? (
        <EmptyState
          icon={<Users size={18} />}
          title={t('pl.cannotApi')}
          body={error}
          action={
            <button className="btn btn-default" onClick={loadProfiles}>
              {t('pl.retry')}
            </button>
          }
        />
      ) : loading ? (
        <p className="px-5 py-8 text-ink-faint">{t('pl.loading')}</p>
      ) : profiles.length === 0 ? (
        <EmptyState
          icon={<Users size={18} />}
          title={t('pl.noProfiles')}
          body={t('pl.noProfilesBody')}
          action={
            <button
              className="btn btn-primary"
              onClick={() => {
                setEditing(null)
                setFormOpen(true)
              }}
            >
              <Plus size={14} strokeWidth={2.5} />
              {t('pl.createFirst')}
            </button>
          }
        />
      ) : visible.length === 0 ? (
        <EmptyState
          icon={<Search size={18} />}
          title={t('pl.noMatches')}
          body={t('pl.noMatchesBody')}
          action={
            <button
              className="btn btn-default"
              onClick={() => {
                setSearch('')
                setStatusFilter('all')
              }}
            >
              {t('pl.clearFilters')}
            </button>
          }
        />
      ) : (
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-line text-left text-[11px] uppercase tracking-[0.05em] text-ink-faint">
              <th className="w-9 py-2 pl-5">
                <input
                  type="checkbox"
                  className="accent-signal"
                  checked={allOnPageSelected}
                  aria-label="Select all on this page"
                  onChange={() =>
                    setSelected((current) => {
                      const next = new Set(current)
                      pageRows.forEach((row) =>
                        allOnPageSelected ? next.delete(row.id) : next.add(row.id),
                      )
                      return next
                    })
                  }
                />
              </th>
              {COLUMN_KEYS.map((column) => (
                <th key={column.key} className="py-2 pr-4 font-medium">
                  <button
                    className="inline-flex items-center gap-1 hover:text-ink"
                    onClick={() => toggleSort(column.key)}
                  >
                    {t(column.labelKey)}
                    {sortKey === column.key &&
                      (sortDir === 'asc' ? <ArrowUp size={11} /> : <ArrowDown size={11} />)}
                  </button>
                </th>
              ))}
              <th className="py-2 pr-4 font-medium">{t('pl.colProxy')}</th>
              <th className="w-[104px] py-2 pr-5" />
            </tr>
          </thead>
          <tbody>
            {pageRows.map((profile, index) => {
              const isRunning = running.has(profile.id)
              const isBusy = busy.has(profile.id)
              return (
                <tr
                  key={profile.id}
                  className="row-in group border-b border-line/60 hover:bg-surface"
                  style={{ animationDelay: `${Math.min(index, 12) * 12}ms` }}
                >
                  <td className="relative py-2.5 pl-5">
                    {/* Hairline marker: a running profile is visible at a glance. */}
                    {isRunning && (
                      <span className="absolute left-0 top-0 h-full w-[2px] bg-signal" aria-hidden />
                    )}
                    <input
                      type="checkbox"
                      className="accent-signal"
                      checked={selected.has(profile.id)}
                      aria-label={t('pl.selectRow', { name: profile.name })}
                      onChange={() =>
                        setSelected((current) => {
                          const next = new Set(current)
                          if (next.has(profile.id)) next.delete(profile.id)
                          else next.add(profile.id)
                          return next
                        })
                      }
                    />
                  </td>

                  <td className="py-2.5 pr-4 font-medium">{profile.name}</td>
                  <td className="py-2.5 pr-4 font-mono text-ink-faint">{profile.id}</td>
                  <td className="py-2.5 pr-4 text-ink-dim">
                    {OS_LABELS[profile.browser_settings?.os ?? ''] ?? profile.browser_settings?.os}
                  </td>
                  <td className="py-2.5 pr-4">
                    <StatusCell status={profile.status} running={isRunning} />
                  </td>
                  <td className="py-2.5 pr-4 text-ink-dim">{formatLastUsed(profile.last_used, lang)}</td>
                  <td className="py-2.5 pr-4">
                    <ProxyCell
                      profile={profile}
                      checking={checkingProxies.has(profile.id)}
                    />
                  </td>

                  <td className="py-2.5 pr-5">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        className="btn btn-default h-7"
                        disabled={isBusy}
                        onClick={() => (isRunning ? stop(profile) : launch(profile))}
                      >
                        {isRunning ? (
                          <>
                            <Square size={11} fill="currentColor" />
                            {t('pl.stop')}
                          </>
                        ) : (
                          <>
                            <Play size={11} fill="currentColor" />
                            {t('pl.run')}
                          </>
                        )}
                      </button>

                      <div className="relative">
                        <button
                          className="btn btn-ghost h-7 w-7 p-0"
                          aria-label={t('pl.actionsFor', { name: profile.name })}
                          aria-haspopup="menu"
                          aria-expanded={menuFor === profile.id}
                          data-menu-trigger={profile.id}
                          onClick={(event) => {
                            event.stopPropagation()
                            setMenuFor(menuFor === profile.id ? null : profile.id)
                          }}
                        >
                          <Ellipsis size={15} />
                        </button>

                        {menuFor === profile.id && (
                          <div
                            role="menu"
                            className="dialog-in absolute right-0 top-8 z-30 w-[164px] overflow-hidden rounded-md border border-line bg-raised py-1 shadow-xl shadow-black/50"
                            onClick={(event) => event.stopPropagation()}
                            onKeyDown={(event) => {
                              const items = Array.from(
                                event.currentTarget.querySelectorAll<HTMLButtonElement>(
                                  '[role="menuitem"]',
                                ),
                              )
                              const index = items.indexOf(document.activeElement as HTMLButtonElement)
                              if (event.key === 'Escape') {
                                event.stopPropagation()
                                closeMenu(profile.id)
                              } else if (event.key === 'ArrowDown') {
                                event.preventDefault()
                                items[(index + 1) % items.length]?.focus()
                              } else if (event.key === 'ArrowUp') {
                                event.preventDefault()
                                items[(index - 1 + items.length) % items.length]?.focus()
                              }
                            }}
                          >
                            <MenuItem
                              icon={<Pencil size={13} />}
                              label={t('pl.mEdit')}
                              autoFocus
                              onClick={() => {
                                setEditing(profile)
                                setFormOpen(true)
                                closeMenu(profile.id)
                              }}
                            />
                            <MenuItem
                              icon={<Copy size={13} />}
                              label={t('pl.mDuplicate')}
                              onClick={() => {
                                clone(profile)
                                closeMenu(profile.id)
                              }}
                            />
                            <MenuItem
                              icon={<Globe size={13} />}
                              label={t('pl.mCheckProxy')}
                              onClick={() => {
                                // The set holds ids, not a count, so a second
                                // check of the same row would clear the first
                                // one's "Checking…" when it finished.
                                if (!checkingProxies.has(profile.id)) checkProxy(profile)
                                closeMenu(profile.id)
                              }}
                            />
                            <MenuItem
                              icon={<PackageOpen size={13} />}
                              label={t('pl.mExport')}
                              onClick={() => {
                                exportArchive(profile)
                                closeMenu(profile.id)
                              }}
                            />
                            <MenuItem
                              icon={<Trash2 size={13} />}
                              label={t('pl.mDelete')}
                              danger
                              onClick={() => {
                                askDelete(profile)
                                closeMenu(profile.id)
                              }}
                            />
                          </div>
                        )}
                      </div>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}

      {totalPages > 1 && (
        <div className="flex items-center justify-between px-5 py-3 text-ink-dim">
          <span>
            {(currentPage - 1) * perPage + 1}–{Math.min(currentPage * perPage, visible.length)} of{' '}
            {visible.length}
          </span>
          <div className="flex items-center gap-1">
            <button
              className="btn btn-ghost h-7 w-7 p-0"
              disabled={currentPage === 1}
              onClick={() => setPage(currentPage - 1)}
              aria-label={t('pl.prevPage')}
            >
              <ChevronLeft size={15} />
            </button>
            <span className="px-2 font-mono">
              {currentPage} / {totalPages}
            </span>
            <button
              className="btn btn-ghost h-7 w-7 p-0"
              disabled={currentPage === totalPages}
              onClick={() => setPage(currentPage + 1)}
              aria-label={t('pl.nextPage')}
            >
              <ChevronRight size={15} />
            </button>
          </div>
        </div>
      )}

      <ProfileForm
        open={formOpen}
        profile={editing}
        onClose={() => setFormOpen(false)}
        onSaved={loadProfiles}
      />

      <ConfirmDialog
        open={confirm !== null}
        title={confirm?.title ?? ''}
        body={confirm?.body ?? ''}
        confirmLabel={confirm?.label}
        destructive={confirm?.label === t('pl.delLabel')}
        onConfirm={runConfirmed}
        onCancel={() => setConfirm(null)}
      />
    </>
  )
}

/** The configured proxy, and under it the last answer it gave.
 *
 * Two lines rather than a column of its own: the table is already wide, and the
 * second line only exists once there is something to say, so a list nobody has
 * checked looks exactly as it did before.
 */
function ProxyCell({ profile, checking }: { profile: Profile; checking: boolean }) {
  const configured = formatProxyString(profile.proxy_config) || '—'
  const check = profile.proxy_check
  const t = useT()

  return (
    // Bounded, because a table cell grows to fit its content: an unreachable
    // proxy reports a whole sentence, and without a cap one dead proxy pushed
    // the row actions off the screen and gave the table a scrollbar.
    <div className="max-w-[280px] leading-tight">
      <div className="truncate font-mono text-ink-faint">{configured}</div>
      {checking ? (
        <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-ink-faint">
          <span className="signal-pulse h-1.5 w-1.5 rounded-full bg-signal" />
          {t('pl.proxyChecking')}
        </div>
      ) : check ? (
        <ProxyResult check={check} />
      ) : null}
    </div>
  )
}

function ProxyResult({ check }: { check: ProxyCheckRecord }) {
  const { lang } = useLang()
  const { tone, label, detail } = readProxyCheck(check, lang)
  const dot = tone === 'ok' ? 'bg-ok' : tone === 'warn' ? 'bg-warn' : 'bg-danger'
  // Latency and country only mean something when the proxy answered; when it did
  // not, the reason is the only thing worth the space.
  const parts = check.reachable
    ? [check.ip, check.country, check.latency_ms !== null ? `${check.latency_ms} ms` : null]
    : [check.error]

  return (
    <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-ink-faint" title={detail}>
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
      <span className="sr-only">{label}.</span>
      {/* min-w-0 so this is the part that gives way: a flex item will not shrink
          below its content without it, and the truncation never happens. */}
      <span className="min-w-0 truncate font-mono">{parts.filter(Boolean).join(' · ')}</span>
      <span className="shrink-0 text-ink-dim/70">· {formatLastUsed(check.checked_at, lang)}</span>
    </div>
  )
}

function StatusCell({ status, running }: { status: string; running: boolean }) {
  const t = useT()
  if (running) {
    return (
      <span className="inline-flex items-center gap-1.5 text-signal">
        <span className="signal-pulse h-1.5 w-1.5 rounded-full bg-signal" />
        {t('pl.statusRunning')}
      </span>
    )
  }
  const tone =
    status === 'blocked' ? 'bg-danger' : status === 'maintenance' ? 'bg-ink-dim' : 'bg-ink-faint'
  return (
    <span className="inline-flex items-center gap-1.5 capitalize text-ink-dim">
      <span className={`h-1.5 w-1.5 rounded-full ${tone}`} />
      {status}
    </span>
  )
}

function MenuItem({
  icon,
  label,
  onClick,
  danger,
  autoFocus,
}: {
  icon: React.ReactNode
  label: string
  onClick: () => void
  danger?: boolean
  autoFocus?: boolean
}) {
  return (
    <button
      role="menuitem"
      autoFocus={autoFocus}
      onClick={onClick}
      className={`flex w-full items-center gap-2.5 px-3 py-1.5 text-left transition-colors hover:bg-line focus:bg-line ${
        danger ? 'text-danger' : 'text-ink'
      }`}
    >
      {icon}
      {label}
    </button>
  )
}
