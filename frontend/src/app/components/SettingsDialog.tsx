'use client';
import { useState } from 'react';

export interface Settings {
  darkMode: boolean;
  delayMin: number;
  delayMax: number;
  largeThreshold: number;
  largePauseSec: number;
  maxPages: number;
  takeScreenshots: boolean;
  downloadImages: boolean;
  downloadVideos: boolean;
}

export const DEFAULT_SETTINGS: Settings = {
  darkMode: true,
  delayMin: 1.5,
  delayMax: 4.5,
  largeThreshold: 20,
  largePauseSec: 120,
  maxPages: 50,
  takeScreenshots: true,
  downloadImages: true,
  downloadVideos: true,
};

export function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem('soupre_settings');
    if (raw) return { ...DEFAULT_SETTINGS, ...JSON.parse(raw) };
  } catch {}
  return { ...DEFAULT_SETTINGS };
}

export function saveSettings(s: Settings) {
  localStorage.setItem('soupre_settings', JSON.stringify(s));
}

interface Props {
  settings: Settings;
  onChange: (s: Settings) => void;
}

export default function SettingsDialog({ settings, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [local, setLocal] = useState<Settings>(settings);

  const dm = settings.darkMode;

  const surface = dm ? 'bg-[#1e1e2e] text-[#cdd6f4] border-[#313244]' : 'bg-white text-[#4c4f69] border-[#ccd0da]';
  const header  = dm ? 'bg-[#181825] border-[#313244]' : 'bg-[#e6e9ef] border-[#ccd0da]';
  const input   = dm
    ? 'bg-[#11111b] border-[#313244] text-[#cdd6f4] focus:border-[#89b4fa]'
    : 'bg-white border-[#ccd0da] text-[#4c4f69] focus:border-[#1e66f5]';
  const label   = dm ? 'text-[#a6adc8]' : 'text-[#6c6f85]';
  const divider = dm ? 'border-[#313244]' : 'border-[#ccd0da]';

  const set = (key: keyof Settings, value: Settings[keyof Settings]) => {
    setLocal(prev => ({ ...prev, [key]: value }));
  };

  const apply = () => {
    saveSettings(local);
    onChange(local);
    setOpen(false);
  };

  const cancel = () => {
    setLocal(settings);
    setOpen(false);
  };

  const formatPause = (sec: number) => {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return m > 0 ? `${m}m ${s}s` : `${s}s`;
  };

  return (
    <>
      {/* Gear trigger button */}
      <button
        onClick={() => { setLocal(settings); setOpen(true); }}
        title="Settings"
        className={`cursor-pointer mt-auto w-10 h-10 flex items-center justify-center rounded-full border transition-colors opacity-70 hover:opacity-100 ${
          dm
            ? 'border-[#313244] hover:bg-[#313244] text-[#a6adc8]'
            : 'border-[#ccd0da] hover:bg-[#ccd0da] text-[#6c6f85]'
        }`}
      >
        <span className="material-symbols-outlined text-[20px]">settings</span>
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={cancel} />
          <div className={`relative w-full max-w-md flex flex-col rounded-xl shadow-2xl border overflow-hidden ${surface}`}>
            {/* Header */}
            <div className={`flex items-center justify-between p-4 border-b ${header}`}>
              <h3 className="font-bold flex items-center gap-2 text-sm">
                <span className="material-symbols-outlined text-[18px]">settings</span>
                Settings
              </h3>
              <button onClick={cancel} className={`cursor-pointer p-1 rounded-md transition-colors ${dm ? 'hover:bg-[#313244]' : 'hover:bg-[#ccd0da]'}`}>
                <span className="material-symbols-outlined text-[18px]">close</span>
              </button>
            </div>

            {/* Body */}
            <div className="p-5 flex flex-col gap-5 overflow-y-auto max-h-[70vh]">

              {/* Theme */}
              <section className="flex flex-col gap-3">
                <p className={`text-xs font-bold uppercase tracking-wider ${label}`}>Appearance</p>
                <label className="flex items-center justify-between cursor-pointer">
                  <span className="text-sm flex items-center gap-2">
                    <span className="material-symbols-outlined text-[16px]">{local.darkMode ? 'dark_mode' : 'light_mode'}</span>
                    {local.darkMode ? 'Dark Mode' : 'Light Mode'}
                  </span>
                  <button
                    type="button"
                    onClick={() => set('darkMode', !local.darkMode)}
                    className={`relative w-11 h-6 rounded-full transition-colors ${
                      local.darkMode ? 'bg-[#89b4fa]' : 'bg-[#ccd0da]'
                    }`}
                  >
                    <span className={`absolute top-1 w-4 h-4 rounded-full bg-white shadow transition-all ${
                      local.darkMode ? 'left-6' : 'left-1'
                    }`} />
                  </button>
                </label>
              </section>

              <div className={`border-t ${divider}`} />

              {/* Rate limiting */}
              <section className="flex flex-col gap-3">
                <p className={`text-xs font-bold uppercase tracking-wider ${label}`}>Rate Limiting</p>

                <div className="grid grid-cols-2 gap-3">
                  <div className="flex flex-col gap-1">
                    <label className={`text-xs ${label}`}>Min delay (s)</label>
                    <input
                      type="number" min={0.5} max={10} step={0.5}
                      value={local.delayMin}
                      onChange={e => set('delayMin', parseFloat(e.target.value))}
                      className={`w-full border rounded-md px-2 py-1.5 text-sm outline-none ${input}`}
                    />
                  </div>
                  <div className="flex flex-col gap-1">
                    <label className={`text-xs ${label}`}>Max delay (s)</label>
                    <input
                      type="number" min={0.5} max={20} step={0.5}
                      value={local.delayMax}
                      onChange={e => set('delayMax', parseFloat(e.target.value))}
                      className={`w-full border rounded-md px-2 py-1.5 text-sm outline-none ${input}`}
                    />
                  </div>
                </div>

                <div className="flex flex-col gap-1">
                  <label className={`text-xs ${label}`}>Large sitemap threshold (pages)</label>
                  <input
                    type="number" min={5} max={200}
                    value={local.largeThreshold}
                    onChange={e => set('largeThreshold', parseInt(e.target.value))}
                    className={`w-full border rounded-md px-2 py-1.5 text-sm outline-none ${input}`}
                  />
                </div>

                <div className="flex flex-col gap-2">
                  <div className="flex justify-between">
                    <label className={`text-xs ${label}`}>Large sitemap pause</label>
                    <span className={`text-xs font-mono font-bold ${dm ? 'text-[#89b4fa]' : 'text-[#1e66f5]'}`}>{formatPause(local.largePauseSec)}</span>
                  </div>
                  <input
                    type="range" min={0} max={1200} step={30}
                    value={local.largePauseSec}
                    onChange={e => set('largePauseSec', parseInt(e.target.value))}
                    className="w-full accent-[#89b4fa]"
                  />
                  <div className={`flex justify-between text-xs ${label}`}>
                    <span>0s</span><span>10m</span><span>20m</span>
                  </div>
                </div>

                <div className="flex flex-col gap-1">
                  <label className={`text-xs ${label}`}>Max pages per sitemap job</label>
                  <input
                    type="number" min={1} max={500}
                    value={local.maxPages}
                    onChange={e => set('maxPages', parseInt(e.target.value))}
                    className={`w-full border rounded-md px-2 py-1.5 text-sm outline-none ${input}`}
                  />
                </div>
              </section>

              <div className={`border-t ${divider}`} />

              {/* Content toggles */}
              <section className="flex flex-col gap-3">
                <p className={`text-xs font-bold uppercase tracking-wider ${label}`}>Content</p>
                {([
                  ['takeScreenshots', 'screenshot', 'Take screenshots'],
                  ['downloadImages',  'image',      'Download images'],
                  ['downloadVideos',  'movie',      'Download videos (yt-dlp)'],
                ] as [keyof Settings, string, string][]).map(([key, icon, text]) => (
                  <label key={key} className="flex items-center justify-between cursor-pointer">
                    <span className="text-sm flex items-center gap-2">
                      <span className="material-symbols-outlined text-[16px]">{icon}</span>
                      {text}
                    </span>
                    <button
                      type="button"
                      onClick={() => set(key, !local[key])}
                      className={`relative w-11 h-6 rounded-full transition-colors ${
                        local[key] ? 'bg-[#89b4fa]' : (dm ? 'bg-[#45475a]' : 'bg-[#ccd0da]')
                      }`}
                    >
                      <span className={`absolute top-1 w-4 h-4 rounded-full bg-white shadow transition-all ${
                        local[key] ? 'left-6' : 'left-1'
                      }`} />
                    </button>
                  </label>
                ))}
              </section>
            </div>

            {/* Footer */}
            <div className={`flex items-center justify-end gap-2 p-4 border-t ${header}`}>
              <button onClick={cancel} className={`cursor-pointer px-4 py-2 rounded-lg text-sm font-bold transition-colors ${
                dm ? 'hover:bg-[#313244] text-[#a6adc8]' : 'hover:bg-[#ccd0da] text-[#6c6f85]'
              }`}>Cancel</button>
              <button onClick={apply} className={`cursor-pointer px-4 py-2 rounded-lg text-sm font-bold transition-colors ${
                dm
                  ? 'bg-[#89b4fa] text-[#11111b] hover:bg-[#b4befe]'
                  : 'bg-[#1e66f5] text-white hover:bg-[#7287fd]'
              }`}>Apply</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
