'use client'

import { useEffect, useMemo, useState } from 'react'
import { Check, Info, LoaderCircle, RefreshCw, TriangleAlert, X } from 'lucide-react'

import { Modal } from '@/components/modal'
import { useToast } from '@/components/toast'
import { useT } from '@/lib/i18n'
import {
  hasGeography,
  isStaleWrite,
  LANGUAGES_FOR_COUNTRY,
  OS_LABELS,
  presetsAPI,
  profilesAPI,
  type DevicePreset,
  type FingerprintSummary,
  type Profile,
  type ProxyCheck,
} from '@/lib/api'

interface FormState {
  name: string
  status: string
  notes: string
  os: string
  timezone: string
  languages: string
  hardwareConcurrency: string
  windowWidth: string
  windowHeight: string
  webrtcMode: string
  stableCanvas: boolean
  geoMode: 'auto' | 'manual'
  latitude: string
  longitude: string
  proxyType: string
  proxyServer: string
  proxyUsername: string
  proxyPassword: string
}

const EMPTY: FormState = {
  name: '',
  status: 'active',
  notes: '',
  os: 'windows',
  timezone: '',
  languages: '',
  hardwareConcurrency: '',
  windowWidth: '1280',
  windowHeight: '720',
  webrtcMode: 'replace',
  stableCanvas: false,
  geoMode: 'auto',
  latitude: '',
  longitude: '',
  proxyType: 'http',
  proxyServer: '',
  proxyUsername: '',
  proxyPassword: '',
}

function fromProfile(profile: Profile): FormState {
  const bs = profile.browser_settings ?? {}
  const proxy = profile.proxy_config ?? {}
  const geo = bs.geolocation
  const lat = geo?.lat ?? geo?.latitude
  const lon = geo?.lon ?? geo?.longitude
  return {
    name: profile.name,
    status: profile.status,
    notes: profile.notes ?? '',
    os: bs.os ?? 'windows',
    timezone: bs.timezone ?? '',
    languages: (bs.languages ?? []).join(', '),
    hardwareConcurrency: bs.hardware_concurrency ? String(bs.hardware_concurrency) : '',
    windowWidth: String(bs.window_width ?? 1280),
    windowHeight: String(bs.window_height ?? 720),
    webrtcMode: bs.webrtc_mode ?? 'replace',
    stableCanvas: bs.stable_canvas ?? false,
    geoMode: lat != null && lon != null ? 'manual' : 'auto',
    latitude: lat != null ? String(lat) : '',
    longitude: lon != null ? String(lon) : '',
    proxyType: proxy.type ?? 'http',
    proxyServer: proxy.server ?? '',
    proxyUsername: proxy.username ?? '',
    proxyPassword: proxy.password ?? '',
  }
}

/**
 * Which fields differ between the profile this form was opened on and the one
 * now stored — the answer to "changed how?", which is the only part of a
 * conflict a user can act on.
 */
function describeChanges(
  before: Profile | null,
  after: Profile,
  t: (key: string, vars?: Record<string, string | number>) => string,
): string {
  if (!before) return ''
  const fields: string[] = []
  if (before.name !== after.name) fields.push(t('fm.chName', { name: after.name }))
  if (before.status !== after.status) fields.push(t('fm.chStatus', { status: after.status }))
  if ((before.notes ?? '') !== (after.notes ?? '')) fields.push(t('fm.chNotes'))
  if (JSON.stringify(before.proxy_config ?? null) !== JSON.stringify(after.proxy_config ?? null))
    fields.push(t('fm.chProxy'))
  if (JSON.stringify(before.browser_settings) !== JSON.stringify(after.browser_settings))
    fields.push(t('fm.chSettings'))
  if (!fields.length) return ''
  // Two is enough to recognise what happened; a full list would not fit a toast.
  const shown = fields.slice(0, 2).join(', ')
  return fields.length > 2 ? `${shown}, ${t('fm.chMore', { n: fields.length - 2 })}` : shown
}

interface Props {
  open: boolean
  /** null = create a new profile. */
  profile: Profile | null
  onClose: () => void
  onSaved: () => void
}

export function ProfileForm({ open, profile, onClose, onSaved }: Props) {
  const isEdit = profile !== null
  const [form, setForm] = useState<FormState>(EMPTY)
  const [saving, setSaving] = useState(false)
  const [regenerating, setRegenerating] = useState(false)
  const [refreshingBrowser, setRefreshingBrowser] = useState(false)
  const [checkingProxy, setCheckingProxy] = useState(false)
  const [proxyCheck, setProxyCheck] = useState<ProxyCheck | null>(null)
  // The `profile` prop is a snapshot from the list; regenerating or updating the
  // browser returns a new machine, and the panel has to show it without waiting
  // for the dialog to be reopened.
  const [machine, setMachine] = useState<FingerprintSummary | null | undefined>(undefined)
  const [reconciling, setReconciling] = useState(false)
  // Whether the *saved* profile states where it is. The form fields alone cannot
  // answer that once the user has started typing in them.
  const [storedGeography, setStoredGeography] = useState(false)
  const [clearingGeography, setClearingGeography] = useState(false)
  const [presets, setPresets] = useState<DevicePreset[]>([])
  const [presetId, setPresetId] = useState('')
  // The version this form was filled from. Sent with the save so that an edit
  // somebody else made in the meantime is refused instead of being overwritten,
  // and moved on after a refused save so a deliberate second Save can proceed.
  const [baseVersion, setBaseVersion] = useState(0)
  const toast = useToast()
  const t = useT()

  useEffect(() => {
    if (open) {
      // The dialog is kept mounted and refilled when it opens, so the form has
      // to be reset here. A key would avoid the effect, but it would also throw
      // away the loaded presets on every open and would not refill when the
      // same profile is reopened after a save changed it.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setForm(profile ? fromProfile(profile) : EMPTY)
      setBaseVersion(profile?.row_version ?? 0)
      setMachine(profile?.fingerprint)
      setStoredGeography(profile ? hasGeography(profile) : false)
      setPresetId('')
    }
  }, [open, profile])

  // Presets only matter while creating: an existing profile is already pinned.
  useEffect(() => {
    if (!open || isEdit || presets.length) return
    presetsAPI
      .list()
      .then(setPresets)
      .catch(() => setPresets([]))
  }, [open, isEdit, presets.length])

  const presetsForOs = useMemo(
    () => presets.filter((preset) => preset.os === form.os),
    [presets, form.os],
  )

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((current) => ({ ...current, [key]: value }))

  /**
   * A key we send is authoritative and a null clears the value, so a blank
   * optional field has to mean different things in the two modes: on create it
   * means "let the backend generate this", so the key is omitted; on edit the
   * user is looking at the stored value and blanking it means "remove it", so
   * an explicit null goes out. Sending null on create produced profiles with a
   * missing timezone and geolocation — a fingerprint with holes in it.
   */
  function buildBrowserSettings() {
    const languages = form.languages
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)

    const settings: Record<string, unknown> = {
      os: form.os,
      window_width: Number(form.windowWidth) || 1280,
      window_height: Number(form.windowHeight) || 720,
      webrtc_mode: form.webrtcMode,
      stable_canvas: form.stableCanvas,
      // Always explicit: "from the proxy IP" must clear any stored coordinates,
      // otherwise geoip stays disabled and the old position keeps applying.
      geolocation:
        form.geoMode === 'manual' && form.latitude && form.longitude
          ? { lat: Number(form.latitude), lon: Number(form.longitude) }
          : null,
    }

    const optional: [string, unknown][] = [
      ['timezone', form.timezone.trim() || null],
      ['languages', languages.length ? languages : null],
      ['hardware_concurrency', form.hardwareConcurrency ? Number(form.hardwareConcurrency) : null],
    ]
    for (const [key, value] of optional) {
      if (value !== null || isEdit) settings[key] = value
    }
    return settings
  }

  function buildProxy() {
    if (!form.proxyServer.trim()) return null
    return {
      type: form.proxyType,
      server: form.proxyServer.trim(),
      username: form.proxyUsername.trim() || undefined,
      password: form.proxyPassword.trim() || undefined,
    }
  }

  /**
   * Checks what is on screen rather than what is stored, so the answer belongs to
   * the proxy the user is currently typing — the point is to find out before
   * saving whether it works and whether it agrees with the profile.
   */
  async function handleCheckProxy() {
    setCheckingProxy(true)
    setProxyCheck(null)
    try {
      setProxyCheck(
        await profilesAPI.checkUnsavedProxy({
          proxy_config: buildProxy(),
          browser_settings: buildBrowserSettings(),
        }),
      )
    } catch (error) {
      toast('error', 'Could not check the proxy', (error as Error).message)
    } finally {
      setCheckingProxy(false)
    }
  }

  /** Fill the Languages field from the checked proxy's country, on request. */
  function handleUseProxyLanguage() {
    const country = proxyCheck?.location?.country
    const languages = country ? LANGUAGES_FOR_COUNTRY[country] : undefined
    if (languages) {
      set('languages', languages)
      toast('ok', t('fm.proxyLangSet'), country ?? '')
    }
  }

  const proxyLanguageAvailable =
    !!proxyCheck?.location?.country &&
    proxyCheck.location.country in LANGUAGES_FOR_COUNTRY

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (!form.name.trim()) {
      toast('error', t('fm.nameRequired'))
      return
    }
    if (form.geoMode === 'manual' && (!form.latitude || !form.longitude)) {
      toast('error', t('fm.manualGeoRequired'))
      return
    }

    setSaving(true)
    try {
      const payload: Record<string, unknown> = {
        name: form.name.trim(),
        notes: form.notes.trim() || null,
        browser_settings: buildBrowserSettings(),
        proxy_config: buildProxy(),
      }

      if (isEdit) {
        payload.status = form.status
        payload.row_version = baseVersion
        await profilesAPI.updateProfile(profile.id, payload)
        toast('ok', t('fm.updated'), form.name.trim())
      } else {
        // The backend generates a consistent fingerprint, then applies these.
        payload.generate_fingerprint = true
        if (presetId) payload.fingerprint_preset = presetId
        await profilesAPI.createProfile(payload)
        toast(
          'ok',
          t('fm.created'),
          presetId ? t('fm.createdPreset', { name: form.name.trim() }) : form.name.trim(),
        )
      }
      onSaved()
      onClose()
    } catch (err) {
      if (isStaleWrite(err) && profile) {
        await handleStaleWrite(profile.id)
        return
      }
      toast('error', isEdit ? t('fm.updateErr') : t('fm.createErr'), String(err))
    } finally {
      setSaving(false)
    }
  }

  /**
   * Somebody else saved this profile while this form was open.
   *
   * Nothing on screen is replaced: the user's typing is theirs, and a form that
   * rewrites itself under them loses work just as surely as the overwrite this
   * refusal prevented. Instead the stored values are fetched to name what
   * actually changed, and the form is moved onto the current version so that a
   * second Save is a deliberate, informed overwrite rather than a blind one.
   */
  async function handleStaleWrite(profileId: string) {
    try {
      const current = await profilesAPI.getProfile(profileId)
      setBaseVersion(current.row_version)
      setStoredGeography(hasGeography(current))
      setMachine(current.fingerprint)
      const changed = describeChanges(profile, current, t)
      toast(
        'error',
        t('fm.staleTitle'),
        changed
          ? `${changed}. ${t('fm.staleRetry')}`
          : t('fm.staleLost'),
      )
    } catch {
      // The reload is a courtesy; without it the user still needs to know the
      // save did not land, and that is the part that must never be swallowed.
      toast('error', t('fm.staleTitle'), t('fm.staleReopen'))
    }
  }

  async function handleRefreshBrowser() {
    if (!profile) return
    setRefreshingBrowser(true)
    try {
      const updated = await profilesAPI.refreshBrowserVersion(profile.id)
      setMachine(updated.fingerprint)
      toast('ok', t('fm.browserUpdated', { v: updated.fingerprint?.browser_major ?? '' }), t('fm.machineUnchanged'))
      onSaved()
    } catch (err) {
      toast('error', t('fm.browserUpdateErr'), String(err))
    } finally {
      setRefreshingBrowser(false)
    }
  }

  /**
   * Keeping the machine only moves the dropdown; keeping the setting replaces the
   * hardware, so the two are reported very differently even though one call does
   * both. The form's own OS field follows, or it would still show the value the
   * user has just resolved away from.
   */
  async function handleReconcileOs(keepMachine: boolean) {
    if (!profile) return
    setReconciling(true)
    try {
      const updated = await profilesAPI.reconcileOs(profile.id, keepMachine)
      setMachine(updated.fingerprint)
      set('os', updated.browser_settings.os)
      toast(
        'ok',
        keepMachine
          ? t('fm.setBack', { os: OS_LABELS[updated.browser_settings.os] ?? updated.browser_settings.os })
          : t('fm.newMachine'),
        keepMachine
          ? t('fm.keepUntouched')
          : t('fm.newMachineDetail', {
              screen: updated.fingerprint?.screen ?? '',
              cores: updated.fingerprint?.hardware_concurrency ?? '',
            }),
      )
      onSaved()
    } catch (err) {
      toast('error', t('fm.reconcileErr'), String(err))
    } finally {
      setReconciling(false)
    }
  }

  async function handleClearGeography() {
    if (!profile) return
    setClearingGeography(true)
    try {
      await profilesAPI.clearGeography([profile.id])
      setForm((current) => ({ ...current, timezone: '', geoMode: 'auto', latitude: '', longitude: '' }))
      setStoredGeography(false)
      toast('ok', t('fm.geoCleared'), t('fm.geoClearedBody'))
      onSaved()
    } catch (err) {
      toast('error', t('fm.geoClearErr'), String(err))
    } finally {
      setClearingGeography(false)
    }
  }

  async function handleRegenerate() {
    if (!profile) return
    setRegenerating(true)
    try {
      const updated = await profilesAPI.resetFingerprint(profile.id)
      setForm(fromProfile(updated))
      setMachine(updated.fingerprint)
      toast('ok', t('fm.regenerated'))
      onSaved()
    } catch (err) {
      toast('error', t('fm.regenerateErr'), String(err))
    } finally {
      setRegenerating(false)
    }
  }

  return (
    <Modal
      open={open}
      title={isEdit ? t('fm.editTitle') : t('fm.newTitle')}
      subtitle={
        isEdit
          ? `${profile.id} · created ${new Date(profile.created_at).toLocaleDateString()}`
          : t('fm.newSubtitle')
      }
      onClose={onClose}
      width={640}
      footer={
        <>
          {isEdit && (
            <button
              type="button"
              className="btn btn-default mr-auto"
              onClick={handleRegenerate}
              disabled={regenerating}
            >
              <RefreshCw size={13} className={regenerating ? 'animate-spin' : ''} />
              {t('fm.regenerate')}
            </button>
          )}
          <button type="button" className="btn btn-default" onClick={onClose}>
            {t('fm.cancel')}
          </button>
          <button type="submit" form="profile-form" className="btn btn-primary" disabled={saving}>
            {saving && <LoaderCircle size={13} className="animate-spin" />}
            {isEdit ? t('fm.save') : t('fm.create')}
          </button>
        </>
      }
    >
      <form id="profile-form" onSubmit={handleSubmit} className="flex flex-col gap-5">
        <Section title={t('fm.secIdentity')}>
          <div className="col-span-2">
            <label className="field-label" htmlFor="pf-name">
              {t('fm.name')}
            </label>
            <input
              id="pf-name"
              className="field"
              value={form.name}
              onChange={(e) => set('name', e.target.value)}
              placeholder="account-1"
              autoFocus
              required
            />
          </div>

          <div>
            <label className="field-label" htmlFor="pf-os">
              {t('fm.os')}
            </label>
            <select
              id="pf-os"
              className="field"
              value={form.os}
              onChange={(e) => set('os', e.target.value)}
            >
              <option value="windows">Windows</option>
              <option value="macos">macOS</option>
              <option value="linux">Linux</option>
            </select>
            {/* Once a machine is pinned this setting is not what a page sees, so
                the honest warning is stronger than "the rest stays as it was". */}
            {isEdit && form.os !== (profile.browser_settings?.os ?? 'windows') && (
              <p className="mt-1.5 text-ink-faint">
                {machine?.pinned_os
                  ? t('fm.osMismatchPinned', {
                      os: OS_LABELS[machine.pinned_os] ?? machine.pinned_os,
                    })
                  : t('fm.osMismatchPlain')}
              </p>
            )}
          </div>

          {isEdit && (
            <div>
              <label className="field-label" htmlFor="pf-status">
                {t('fm.status')}
              </label>
              <select
                id="pf-status"
                className="field"
                value={form.status}
                onChange={(e) => set('status', e.target.value)}
              >
                <option value="active">{t('pl.optActive')}</option>
                <option value="inactive">{t('pl.optInactive')}</option>
                <option value="blocked">{t('pl.optBlocked')}</option>
                <option value="maintenance">{t('pl.optMaintenance')}</option>
              </select>
            </div>
          )}

          <div className="col-span-2">
            <label className="field-label" htmlFor="pf-notes">
              {t('fm.notes')}
            </label>
            <textarea
              id="pf-notes"
              className="field resize-y"
              rows={2}
              value={form.notes}
              onChange={(e) => set('notes', e.target.value)}
            />
          </div>
        </Section>

        <Section title={t('fm.secProxy')} hint={t('fm.proxyHint')}>
          <div>
            <label className="field-label" htmlFor="pf-proxy-type">
              {t('fm.proxyType')}
            </label>
            <select
              id="pf-proxy-type"
              className="field"
              value={form.proxyType}
              onChange={(e) => set('proxyType', e.target.value)}
            >
              <option value="http">HTTP</option>
              <option value="https">HTTPS</option>
              <option value="socks4">SOCKS4</option>
              <option value="socks5">SOCKS5</option>
            </select>
          </div>

          <div>
            <label className="field-label" htmlFor="pf-proxy-server">
              {t('fm.proxyServer')}
            </label>
            <input
              id="pf-proxy-server"
              className="field font-mono"
              value={form.proxyServer}
              onChange={(e) => set('proxyServer', e.target.value)}
              placeholder="host:port"
            />
          </div>

          <div>
            <label className="field-label" htmlFor="pf-proxy-user">
              {t('fm.proxyUsername')}
            </label>
            <input
              id="pf-proxy-user"
              className="field"
              value={form.proxyUsername}
              onChange={(e) => set('proxyUsername', e.target.value)}
              autoComplete="off"
            />
          </div>

          <div>
            <label className="field-label" htmlFor="pf-proxy-pass">
              {t('fm.proxyPassword')}
            </label>
            <input
              id="pf-proxy-pass"
              type="password"
              className="field"
              value={form.proxyPassword}
              onChange={(e) => set('proxyPassword', e.target.value)}
              autoComplete="new-password"
            />
          </div>

          {/* Firefox refuses SOCKS credentials outright, so the browser would
              fail to start rather than fall back — say so before that happens. */}
          {form.proxyType.startsWith('socks') &&
            (form.proxyUsername.trim() || form.proxyPassword.trim()) && (
              <p className="col-span-2 text-danger">{t('fm.socksWarning')}</p>
            )}

          <div className="col-span-2">
            <button
              type="button"
              className="btn"
              onClick={handleCheckProxy}
              disabled={checkingProxy}
            >
              {checkingProxy ? (
                <LoaderCircle size={13} className="animate-spin" />
              ) : (
                <RefreshCw size={13} />
              )}
              {checkingProxy ? t('fm.checkingProxy') : t('fm.checkProxy')}
            </button>
            {proxyLanguageAvailable && (
              <button type="button" className="btn" onClick={handleUseProxyLanguage}>
                {t('fm.useProxyLang')}
              </button>
            )}
            <ProxyCheckResult result={proxyCheck} />
          </div>
        </Section>

        {isEdit ? (
          <PinnedMachine
            fingerprint={machine}
            onRefreshBrowser={handleRefreshBrowser}
            refreshing={refreshingBrowser}
            onReconcileOs={handleReconcileOs}
            reconciling={reconciling}
          />
        ) : (
          <fieldset>
            <legend className="mb-0.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
              {t('fm.secMachine')}
            </legend>
            <p className="mb-2.5 text-ink-faint">{t('fm.machineHint')}</p>
            <select
              className="field"
              value={presetId}
              onChange={(event) => setPresetId(event.target.value)}
              aria-label={t('fm.devicePreset')}
            >
              <option value="">{t('fm.generateAuto')}</option>
              {presetsForOs.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {[preset.screen, `${preset.hardware_concurrency} cores`, shortGpu(preset.gpu)]
                    .filter(Boolean)
                    .join(' · ')}
                </option>
              ))}
            </select>
            {presetsForOs.length > 0 && (
              <p className="mt-1.5 text-ink-faint">
                {t('fm.devicesAvailable', { n: presetsForOs.length, os: form.os })}
              </p>
            )}
          </fieldset>
        )}

        <Section title={t('fm.secFingerprint')} hint={t('fm.fingerprintHint')}>
          <div>
            <label className="field-label" htmlFor="pf-tz">
              {t('fm.timezone')}
            </label>
            <input
              id="pf-tz"
              className="field font-mono"
              value={form.timezone}
              onChange={(e) => set('timezone', e.target.value)}
              placeholder="Europe/Berlin"
            />
          </div>

          <div>
            <label className="field-label" htmlFor="pf-langs">
              {t('fm.languages')}
            </label>
            <input
              id="pf-langs"
              className="field font-mono"
              value={form.languages}
              onChange={(e) => set('languages', e.target.value)}
              placeholder="en-US, en"
            />
          </div>

          <div>
            <label className="field-label" htmlFor="pf-cores">
              {t('fm.cpuCores')}
            </label>
            <input
              id="pf-cores"
              type="number"
              min={1}
              max={32}
              className="field"
              value={form.hardwareConcurrency}
              onChange={(e) => set('hardwareConcurrency', e.target.value)}
              placeholder={t('fm.auto')}
            />
          </div>

          <div>
            <label className="field-label" htmlFor="pf-webrtc">
              {t('fm.webrtc')}
            </label>
            <select
              id="pf-webrtc"
              className="field"
              value={form.webrtcMode}
              onChange={(e) => set('webrtcMode', e.target.value)}
            >
              <option value="replace">{t('fm.webrtcReplace')}</option>
              <option value="real">{t('fm.webrtcReal')}</option>
              <option value="forward">{t('fm.webrtcForward')}</option>
              <option value="none">{t('fm.webrtcNone')}</option>
            </select>
          </div>

          <div className="col-span-2">
            <label className="field-label" htmlFor="pf-canvas">
              {t('fm.canvas')}
            </label>
            <select
              id="pf-canvas"
              className="field"
              value={form.stableCanvas ? 'stable' : 'randomised'}
              onChange={(e) => set('stableCanvas', e.target.value === 'stable')}
            >
              <option value="randomised">{t('fm.canvasRandom')}</option>
              <option value="stable">{t('fm.canvasStable')}</option>
            </select>
            <p className="mt-1.5 text-ink-faint">
              {form.stableCanvas ? t('fm.canvasStableHint') : t('fm.canvasRandomHint')}
            </p>
          </div>

          <div>
            <label className="field-label" htmlFor="pf-win-w">
              {t('fm.windowWidth')}
            </label>
            <input
              id="pf-win-w"
              type="number"
              className="field"
              value={form.windowWidth}
              onChange={(e) => set('windowWidth', e.target.value)}
            />
          </div>

          <div>
            <label className="field-label" htmlFor="pf-win-h">
              {t('fm.windowHeight')}
            </label>
            <input
              id="pf-win-h"
              type="number"
              className="field"
              value={form.windowHeight}
              onChange={(e) => set('windowHeight', e.target.value)}
            />
          </div>

          <div>
            <label className="field-label" htmlFor="pf-geo">
              {t('fm.geolocation')}
            </label>
            <select
              id="pf-geo"
              className="field"
              value={form.geoMode}
              onChange={(e) => set('geoMode', e.target.value as 'auto' | 'manual')}
            >
              <option value="auto">{t('fm.geoAuto')}</option>
              <option value="manual">{t('fm.geoManual')}</option>
            </select>
          </div>

          {form.geoMode === 'manual' && (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="field-label" htmlFor="pf-lat">
                  {t('fm.latitude')}
                </label>
                <input
                  id="pf-lat"
                  className="field font-mono"
                  value={form.latitude}
                  onChange={(e) => set('latitude', e.target.value)}
                  placeholder="52.52"
                />
              </div>
              <div>
                <label className="field-label" htmlFor="pf-lon">
                  {t('fm.longitude')}
                </label>
                <input
                  id="pf-lon"
                  className="field font-mono"
                  value={form.longitude}
                  onChange={(e) => set('longitude', e.target.value)}
                  placeholder="13.405"
                />
              </div>
            </div>
          )}

          {/* Until recently every new profile was given the timezone and
              coordinates of a randomly chosen region, so an old one can claim
              Shanghai on a German proxy. Nothing recorded which values were
              chosen and which were rolled, so this is offered whenever both are
              set rather than guessed at. */}
          {isEdit && storedGeography && (
            <div className="col-span-2 rounded-md border border-line bg-raised p-2.5">
              <p className="text-ink">{t('fm.storedGeoTitle')}</p>
              <p className="mt-0.5 text-ink-faint">{t('fm.storedGeoBody')}</p>
              <div className="mt-2 flex justify-end">
                <button
                  type="button"
                  className="btn btn-default"
                  onClick={handleClearGeography}
                  disabled={clearingGeography}
                >
                  {clearingGeography && <LoaderCircle size={13} className="animate-spin" />}
                  {t('fm.clearGeoFollow')}
                </button>
              </div>
            </div>
          )}
        </Section>
      </form>
    </Modal>
  )
}

/**
 * Reduce a renderer string to the card name.
 *
 *   "ANGLE (NVIDIA, NVIDIA GeForce GTX 980 Direct3D11 vs_5_0 ps_5_0)" -> "NVIDIA GeForce GTX 980"
 *   "Apple M1, or similar"                                           -> "Apple M1"
 *
 * Unwrapping ANGLE first matters: vendor names contain their own brackets
 * ("Intel(R) HD Graphics"), so cutting at the first one truncates the name.
 */
function shortGpu(gpu?: string | null): string {
  if (!gpu) return ''
  // Drop Camoufox's ", or similar" suffix before unwrapping, or the closing
  // bracket of the ANGLE wrapper is no longer at the end of the string.
  const base = gpu.replace(/,\s*or similar\s*$/i, '')
  const angle = base.match(/^ANGLE \([^,]+,\s*(.*)\)$/)
  const name = angle ? angle[1] : base
  return name.split(/\s+Direct3D|\s+vs_/)[0].trim()
}

/**
 * The machine a profile is pinned to. Without this the profile would look like
 * different hardware every session, so it is worth showing that it does not.
 */
function PinnedMachine({
  fingerprint,
  onRefreshBrowser,
  refreshing,
  onReconcileOs,
  reconciling,
}: {
  fingerprint?: FingerprintSummary | null
  onRefreshBrowser: () => void
  refreshing: boolean
  onReconcileOs: (keepMachine: boolean) => void
  reconciling: boolean
}) {
  const t = useT()
  if (!fingerprint) {
    return (
      <fieldset>
        <legend className="mb-0.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
          {t('fm.secMachine')}
        </legend>
        <p className="text-ink-faint">{t('fm.machineUnpinned')}</p>
      </fieldset>
    )
  }

  const rows: [string, string | number | null | undefined][] = [
    [t('fm.rowBrowser'), fingerprint.browser_major ? `Firefox ${fingerprint.browser_major}` : null],
    [t('fm.rowScreen'), fingerprint.screen],
    [t('fm.rowCores'), fingerprint.hardware_concurrency],
    [t('fm.rowGpu'), fingerprint.gpu],
    [t('fm.rowFonts'), fingerprint.font_count],
    [t('fm.rowUA'), fingerprint.user_agent],
  ]

  return (
    <fieldset>
      <legend className="mb-0.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
        {t('fm.secMachine')}
      </legend>
      <p className="mb-2.5 text-ink-faint">
        {t('fm.machinePinned', { n: fingerprint.property_count ?? 0 })}
      </p>
      <div className="panel divide-y divide-line">
        {rows
          .filter(([, value]) => value !== null && value !== undefined && value !== '')
          .map(([label, value]) => (
            <div key={label} className="flex gap-3 px-3 py-1.5">
              <span className="w-[92px] shrink-0 text-ink-dim">{label}</span>
              <span className="min-w-0 flex-1 break-all font-mono text-ink">{value}</span>
            </div>
          ))}
      </div>

      {/* A pin never ages by itself. A profile kept for months keeps claiming the
          browser it was created with, and a version well behind is itself odd —
          so offer the update a real machine would have taken. */}
      {fingerprint.browser_outdated && (
        <div className="mt-2 flex items-start gap-2.5 rounded-md border border-line bg-raised p-2.5">
          <TriangleAlert size={14} className="mt-0.5 shrink-0 text-signal" />
          <div className="flex-1">
            <p className="text-ink">
              {t('fm.outdatedTitle', {
                old: fingerprint.browser_major ?? '',
                new: fingerprint.installed_major ?? '',
              })}
            </p>
            <p className="mt-0.5 text-ink-faint">{t('fm.outdatedBody')}</p>
          </div>
          <button
            type="button"
            className="btn btn-default shrink-0"
            onClick={onRefreshBrowser}
            disabled={refreshing}
          >
            {refreshing && <LoaderCircle size={13} className="animate-spin" />}
            {t('fm.update')}
          </button>
        </div>
      )}

      {/* The OS dropdown can be changed long after the machine was pinned, and
          nothing stops it — the pin is what a page sees, so the setting simply
          stops meaning anything. The two ways out cost very different amounts,
          so neither is preselected. */}
      {fingerprint.os_mismatch && (
        <div className="mt-2 rounded-md border border-line bg-raised p-2.5">
          <div className="flex items-start gap-2.5">
            <TriangleAlert size={14} className="mt-0.5 shrink-0 text-warn" />
            <div>
              <p className="text-ink">
                {t('fm.osMismatchTitle', {
                  wanted: osLabel(fingerprint.settings_os, t('fm.osUnknown')),
                  pinned: osLabel(fingerprint.pinned_os, t('fm.osUnknown')),
                })}
              </p>
              <p className="mt-0.5 text-ink-faint">
                {t('fm.osMismatchBody', {
                  wanted: osLabel(fingerprint.settings_os, t('fm.osUnknown')),
                  pinned: osLabel(fingerprint.pinned_os, t('fm.osUnknown')),
                })}
              </p>
            </div>
          </div>
          <div className="mt-2 flex justify-end gap-2">
            <button
              type="button"
              className="btn btn-default"
              onClick={() => onReconcileOs(true)}
              disabled={reconciling}
            >
              {reconciling && <LoaderCircle size={13} className="animate-spin" />}
              {t('fm.keepMachine')}
            </button>
            <button
              type="button"
              className="btn btn-default"
              onClick={() => onReconcileOs(false)}
              disabled={reconciling}
            >
              {t('fm.newOsMachine', { os: osLabel(fingerprint.settings_os, t('fm.osUnknown')) })}
            </button>
          </div>
        </div>
      )}
    </fieldset>
  )
}

function osLabel(os?: string | null, unknown = 'unknown'): string {
  return (os && OS_LABELS[os]) || os || unknown
}
function Section({
  title,
  hint,
  children,
}: {
  title: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <fieldset>
      <legend className="mb-0.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
        {title}
      </legend>
      {hint && <p className="mb-2.5 text-ink-faint">{hint}</p>}
      <div className="grid grid-cols-2 gap-3">{children}</div>
    </fieldset>
  )
}


/**
 * What the check found: where the proxy actually comes out, and everything a page
 * could notice between that and the profile. A working proxy is not the whole
 * answer — a profile whose timezone contradicts its exit address is detectable
 * however healthy the connection is.
 *
 * Three levels, three treatments: red for something that stops the browser
 * launching, amber for something a page can detect, grey for a note.
 */
function ProxyCheckResult({ result }: { result: ProxyCheck | null }) {
  const t = useT()
  if (!result) return null

  if (!result.reachable) {
    return (
      <p className="mt-2 flex items-start gap-1.5 text-danger">
        <X size={13} className="mt-0.5 shrink-0" />
        <span>{result.error}</span>
      </p>
    )
  }

  const where = result.location
  const place = [where?.country, where?.timezone].filter(Boolean).join(' · ')

  return (
    <div className="mt-2 space-y-1.5">
      <p className="flex items-start gap-1.5 text-ink-muted">
        <Check size={13} className="mt-0.5 shrink-0 text-ok" />
        <span>
          {t('fm.exitsAt')} <span className="font-mono">{where?.ip}</span>
          {place && <> — {place}</>}
          {result.latency_ms !== null && <> · {result.latency_ms} ms</>}
        </span>
      </p>
      {result.findings.map((finding, index) => (
        <p
          key={index}
          className={`flex items-start gap-1.5 ${
            finding.level === 'error'
              ? 'text-danger'
              : finding.level === 'warning'
                ? 'text-warn'
                : 'text-ink-faint'
          }`}
        >
          {finding.level === 'info' ? (
            <Info size={13} className="mt-0.5 shrink-0" />
          ) : (
            <TriangleAlert size={13} className="mt-0.5 shrink-0" />
          )}
          <span>{finding.message}</span>
        </p>
      ))}
    </div>
  )
}
